#!/usr/bin/env python3
"""THE DOC REFERENCES: every place a repository's docs cite its tree is pinned to the content it was last read against.

    bazel test //:docrefs                       # the gate: every reference pinned and read, none pending or broken
    bazel run //:docrefs -- pending            # the references whose target changed or vanished, with the change
    bazel run //:docrefs -- accept DOC...      # pin DOC's new and changed references, read against their targets
    bazel run //:docrefs -- accept --whole DOC  # the whole of DOC was read: every reference in it is read
    bazel run //:docrefs -- prune              # drop the pins of references no doc makes any more
    bazel run //:docrefs -- init               # pin today's references, unread; refused when a lock exists
    bazel run //:docrefs -- move OLD NEW       # a path moved: its citations and pins follow, the content proven the same

A repository runs it through its `docrefs_test` target (`bazel/rules.bzl`), whose attributes are the repository's
configuration, handed here as flags: the lock (`--lock`), the package roots a dotted name resolves under (`--package
NAME=DIR`), the mirror rules (`--mirror DOCS=SOURCES`), the docs never read (`--exclude`), the pinned data (`--data`)
and the target's own label (`--label`). It runs in the checkout, on the standard library only.

A REFERENCE is a doc's citation of a primitive: `` `tools/ratify.py`'s `main` ``, a dotted name in code font whose
first component is a package root (`` `fsla.ops.carry.carry_forward` ``), a path in code font, a path into the pinned
data (`data:lab/...`), or a link (`[x](build.md#wheels)`). Its TARGET is a function, method, class or constant (Python,
found by `ast`); a function or a `//: @ref NAME` ... `//: @end` region (C++/CUDA); a markdown section, from its heading
to the next heading at its level or above; or a whole file or directory, which is checked to exist and never hashed. A
target's content is normalized before it is hashed -- Python through `ast.unparse` with docstrings dropped, everything
else with comments dropped and whitespace collapsed -- so a reformat or a comment edit moves no pin.

The lock holds one pin per (doc, target): the digest, the commit it was pinned at, and whether a docs pass has read it
(`init` pins today's references UNREAD). A pinned reference whose target's digest moved is PENDING, and so is one whose
target vanished. THE GATE FAILS ON ANY REFERENCE NOT CLEAN (a commit that changes what a doc cites re-reads and re-pins
those references in the same commit): one the lock does not hold, one pending, one unread, one that resolves to
nothing, and a pin whose reference no doc makes any more (`prune`). Its message names each citing doc:line and target
and the one command that re-pins after the sentence was read against the target.
Docs: fsla-devtools' README.md#the-doc-references.
"""
from __future__ import annotations

import argparse
import ast
import copy
import difflib
import functools
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

#: A path in code font is a reference when it ends in one of these, or in `/`.
PATH_SUFFIXES = (".py", ".md", ".cu", ".cuh", ".cpp", ".h", ".hpp", ".json", ".toml", ".yaml", ".yml", ".sh", ".in",
                 ".cfg", ".txt", ".lock")
CPP_SUFFIXES = (".cu", ".cuh", ".cpp", ".h", ".hpp")
#: the sources a mirror page's stem is looked for in
MIRROR_SUFFIXES = (*CPP_SUFFIXES, ".py", ".sh")

#: A path in code font that starts `data:` names a file of THE PINNED DATA (a data repository at one commit, the
#: environment extension's `data` tag), which the check is handed by its rule (`--data`): its root is that file's directory
DATA_PREFIX = "data:"


@dataclass(frozen=True)
class Repo:
    """The repository the references are read in, and its configuration (its `docrefs_test` target's attributes)."""

    root: Path
    lock: str  #: the lock's path, repo-relative
    #: a dotted name in code font is a reference when its first component is one of these; the value is the directory
    #: the package imports from ("" for the root)
    packages: dict[str, str] = field(default_factory=dict)
    #: MIRROR RULES: a doc under the key's prefix mirrors the source at the value's prefix and the same relative stem
    #: (`docs/internals/` -> `csrc/src/`: `docs/internals/a/b.md` mirrors `csrc/src/a/b.cu`), in order
    mirrors: dict[str, str] = field(default_factory=dict)
    #: docs never read: a ledger whose rows describe the tree AT THEIR COMMIT, never re-pinned to a later tree
    exclude: tuple[str, ...] = ()
    data: Path | None = None  #: the pinned data's root directory
    label: str = "//:docrefs"  #: the target the messages name


#: the repository this run reads (`main` sets it from its flags; a test sets its own)
REPO: Repo


@dataclass(frozen=True, order=True)
class Target:
    path: str  #: repo-relative
    name: str = ""  #: a primitive's qualified name, a markdown anchor, or "" for the whole file

    def __str__(self) -> str:
        return f"{self.path}#{self.name}" if self.path.endswith(".md") and self.name else (
            f"{self.path}::{self.name}" if self.name else self.path)

    @classmethod
    def spelled(cls, text: str) -> Target:
        """The target a lock row spells (`__str__`'s inverse)."""
        path, sep, name = text.partition("::")
        if not sep and ".md#" in text:
            path, _, name = text.partition("#")
        return cls(path, name)


def tracked() -> set[str]:
    out = subprocess.run(["git", "ls-files"], cwd=REPO.root, capture_output=True, text=True, check=True).stdout
    return set(out.splitlines())


def docs(files: set[str]) -> list[str]:
    return sorted(f for f in files if f.endswith(".md") and f not in REPO.exclude)


# ------------------------------------------------------------------------------------------------ finding references

def _prose_lines(text: str):
    """``(lineno, line)`` outside fenced code blocks."""
    fenced = False
    for lineno, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if not fenced:
            yield lineno, line


POSSESSIVE = re.compile(r"`([^`\s]+)`'s `([A-Za-z_][\w.]*)(?:\(\))?`")
CODE = re.compile(r"`([^`\n]+)`")
LINK = re.compile(r"\]\(([^)\s]+)\)")
DOTTED = re.compile(r"^([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)(?:\(\))?$")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
EXPLICIT_ANCHOR = re.compile(r'<a id="([A-Za-z0-9_-]+)"')


def _module_file(parts: list[str], files: set[str]) -> str | None:
    stem = "/".join([*filter(None, [REPO.packages[parts[0]]]), *parts])
    for candidate in (f"{stem}.py", f"{stem}/__init__.py"):
        if candidate in files:
            return candidate
    return None


def _dotted(span: str, files: set[str]) -> Target | None:
    match = DOTTED.match(span)
    if not match or match.group(1).split(".")[0] not in REPO.packages:
        return None
    parts = match.group(1).split(".")
    for k in range(len(parts), 0, -1):
        module = _module_file(parts[:k], files)
        if module:
            return Target(module, ".".join(parts[k:]))
    return Target("/".join(parts) + ".py", "")  #: a name under our packages that is no module: broken


def _data(span: str) -> Target | None:
    """A file or directory of the pinned data (`data:lab/<experiment>/<arch>.json`), a path inside the data repository
    at the commit the workspace pins: checked to exist there, never hashed -- the pin is the content's."""
    if not span.startswith(DATA_PREFIX) or any(c in span for c in " <>*{}$~|,;=()[]…"):
        return None
    return Target(span.rstrip("/"))


def _path(span: str, files: set[str]) -> Target | None:
    span = span.split("::")[0]
    if any(c in span for c in " <>*{}$~|,;=()[]…") or span.startswith(("/", "http", "-", "./", "../")) or "/" not in span:
        return None
    path, _, anchor = span.partition("#")
    path = path.split(":")[0]
    if not (path.endswith(PATH_SUFFIXES) or path.endswith("/")):
        return None
    if path.split("/")[0] not in {f.split("/")[0] for f in files}:
        return None
    return Target(path.rstrip("/") if path.endswith("/") else path, anchor if path.endswith(".md") else "")


def _resolve_path(name: str, files: set[str]) -> str | None:
    """A path as a doc writes it: repo-relative, or the unique tracked file it is a trailing part of."""
    if name in files or any(f.startswith(name.rstrip("/") + "/") for f in files):
        return name
    hits = [f for f in files if f.endswith("/" + name)]
    return hits[0] if len(hits) == 1 else None


def _link_target(doc: str, link: str) -> Target:
    """A markdown link's file, relative to the doc, and its section when it names one."""
    path, _, anchor = link.partition("#")
    parts: list[str] = []
    for part in (doc if not path else f"{Path(doc).parent.as_posix()}/{path}").split("/"):
        if part == "..":
            if parts:
                parts.pop()
        elif part not in ("", "."):
            parts.append(part)
    resolved = "/".join(parts)
    return Target(resolved, anchor if resolved.endswith(".md") else "")


def mirrored(doc: str, files: set[str]) -> list[str]:
    """The sources a MIRROR PAGE describes: a doc under a mirror rule's doc prefix mirrors the source at each matching
    rule's source prefix and the doc's stem, in each source suffix the tree carries, plus any path in code font on the
    page's title line or in its opening `Mirrors ...` / `The mirror of ...` sentence."""
    stems = [source + doc[len(prefix):-len(".md")] for prefix, source in REPO.mirrors.items() if doc.startswith(prefix)]
    if not stems:
        return []
    sources = [f"{stem}{suffix}" for stem in stems for suffix in MIRROR_SUFFIXES if f"{stem}{suffix}" in files]
    head = (REPO.root / doc).read_text().splitlines()[:8]
    for line in head:
        if line.startswith("# ") or re.match(r"^(Mirrors|The mirror of) `", line):
            sources += [p for p in CODE.findall(line) if p in files and p not in sources]
    return sources


def references(doc: str, files: set[str], pinned: frozenset[str] = frozenset()) -> dict[Target, int]:
    """Every target ``doc`` cites, with the first line citing it. On a mirror page a bare name in code font cites the
    mirrored source's primitive of that name when it resolves there, or when ``pinned`` (the doc's pinned targets)
    already holds it -- a pinned name whose primitive left is a vanished target, not a citation that went away."""
    found: dict[Target, int] = {}
    sources = mirrored(doc, files)
    version = Version(frozenset(files))
    for lineno, line in _prose_lines((REPO.root / doc).read_text()):
        rest = line
        for path, name in POSSESSIVE.findall(line):
            if not path.endswith(PATH_SUFFIXES):
                continue
            found.setdefault(Target(_resolve_path(path, files) or path, name), lineno)
            rest = rest.replace(f"`{path}`'s `{name}`", "")
        for span in CODE.findall(rest):
            target = _dotted(span, files) or _data(span) or _path(span, files)
            if target:
                found.setdefault(target, lineno)
                continue
            bare = re.fullmatch(r"([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?)(?:\(\))?", span)
            for source in sources if bare else ():
                candidate = Target(source, bare.group(1))
                if str(candidate) in pinned or locate(candidate, version) not in (None, ("", "")):
                    found.setdefault(candidate, lineno)
                    break
        for link in LINK.findall(rest):
            if not link.startswith(("http:", "https:", "mailto:")):
                found.setdefault(_link_target(doc, link), lineno)
    return found


# ------------------------------------------------------------------------------------------------ reading a target

def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _strip_docstrings(node: ast.AST) -> ast.AST:
    node = copy.deepcopy(node)
    for inner in ast.walk(node):
        body = getattr(inner, "body", None)
        if (isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)) and body
                and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            inner.body = body[1:] or [ast.Pass()]
    return node


@dataclass(frozen=True)
class Version:
    """The tree a target is read in: the working tree, or a commit (`pending` reads a pin's)."""

    files: frozenset[str]
    commit: str | None = None

    def text(self, path: str) -> str | None:
        return _text(REPO.root, self.commit, path)


@functools.cache
def _text(root: Path, commit: str | None, path: str) -> str | None:
    if commit is None:
        return (root / path).read_text(errors="replace") if (root / path).is_file() else None
    shown = subprocess.run(["git", "show", f"{commit}:{path}"], cwd=root, capture_output=True, text=True)
    return shown.stdout if shown.returncode == 0 else None


@functools.cache
def _parsed(text: str) -> ast.Module:
    return ast.parse(text)


def _binds(stmt: ast.stmt, name: str) -> bool:
    """Whether ``stmt`` defines ``name``: a function, a class, or an assignment to it."""
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return stmt.name == name
    targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target] if isinstance(stmt, ast.AnnAssign) else []
    return any(isinstance(t, ast.Name) and t.id == name for t in targets)


def _python(version: Version, path: str, name: str) -> tuple[str, str] | None:
    """``(source, normalized)`` of ``name`` in ``path``, following up to four `from x import name` re-exports."""
    for _ in range(5):
        text = version.text(path)
        scope: list[ast.stmt] = _parsed(text).body
        parts, node, moved = name.split("."), None, False
        for i, part in enumerate(parts):
            node = next((stmt for stmt in scope if _binds(stmt, part)), None)
            if node is None and i == 0:
                for stmt in scope:
                    if not (isinstance(stmt, ast.ImportFrom) and stmt.module and stmt.level == 0
                            and stmt.module.split(".")[0] in REPO.packages):
                        continue
                    module = _module_file(stmt.module.split("."), version.files)
                    alias = next((a for a in stmt.names if (a.asname or a.name) == part), None)
                    if alias and module:
                        path, name, moved = module, ".".join([alias.name, *parts[1:]]), True
                        break
            if node is None:
                break
            scope = getattr(node, "body", [])
        if moved:
            continue
        if node is None:
            return None
        return ast.get_source_segment(text, node, padded=True) or "", ast.unparse(_strip_docstrings(node))
    return None


def _without_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return " ".join(re.sub(r"//.*", " ", text).split())


def _cpp(text: str, name: str) -> str | None:
    """A ``//: @ref name`` region, else the first brace-matched definition of ``name``; "" when the file only names
    it."""
    region = re.search(rf"//:\s*@ref\s+{re.escape(name)}\s*\n(.*?)//:\s*@end\b", text, flags=re.S)
    if region:
        return region.group(1)
    for match in re.finditer(rf"\b{re.escape(name)}\s*(?:<[^;{{}}]*>)?\s*\(", text):
        line = text[text.rfind("\n", 0, match.start()) + 1:match.start()]
        if not re.search(r"[\w*&>:]\s*$", line) or re.match(r"\s*(if|for|while|switch|return|else)\b", line):
            continue  #: a call, not a definition: a definition's name follows its return type
        head_end = text.find("{", match.end())
        if head_end < 0 or ";" in text[match.end():head_end]:
            continue
        depth, i = 0, head_end
        while i < len(text):
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            if depth == 0:
                return text[text.rfind("\n", 0, match.start()) + 1:i + 1]
            i += 1
    return "" if re.search(rf"\b{re.escape(name)}\b", text) else None


def _slug(heading: str) -> str:
    """The anchor a heading gets, by GitHub's rule: lowercase, punctuation dropped, spaces to dashes, and `-` and `_`
    KEPT. The underscore is an emphasis marker between words and a letter inside one (`sm_86`), and GitHub keeps it
    either way; stripping it here made every heading naming a snake_case symbol unreachable by its real anchor."""
    heading = re.sub(r"[`*]", "", heading).strip().lower()
    return re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", heading))


def _section(text: str, anchor: str) -> str | None:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        heading = HEADING.match(line)
        #: EVERY explicit anchor on the line: a heading renamed in place keeps its old id beside its new one, so the
        #: second `<a id=...>` is exactly the link a doc written before the rename still uses
        named = anchor in {m.group(1) for m in EXPLICIT_ANCHOR.finditer(line)}
        if (heading and _slug(heading.group(2)) == anchor) or named:
            level = len(heading.group(1)) if heading else 7
            j = i + 1
            while j < len(lines) and not ((h := HEADING.match(lines[j])) and (len(h.group(1)) <= level or not heading)):
                j += 1
            return "\n".join(lines[i:j])
    return None


def _generated(path: str) -> bool:
    """A build's output the tree ignores (a generated module): a doc may cite it, and no checkout has it."""
    return subprocess.run(["git", "check-ignore", "-q", path], cwd=REPO.root).returncode == 0


def locate(target: Target, version: Version) -> tuple[str, str] | None:
    """``(source, normalized)``: the target's text as written and as hashed; ``("", "")`` for a target checked only to
    exist; None when it resolves to nothing."""
    if target.path.startswith(DATA_PREFIX):
        if REPO.data is None:
            raise SystemExit("docrefs: a doc cites the pinned data and no data was handed (`--data`, the rule's `data`)")
        return ("", "") if (REPO.data / target.path[len(DATA_PREFIX):]).exists() else None
    if not (target.path in version.files or any(f.startswith(target.path + "/") for f in version.files)):
        return ("", "") if version.commit is None and _generated(target.path) else None
    text = version.text(target.path)
    if not target.name or text is None:
        return ("", "") if not target.name else None
    if target.path.endswith(".py"):
        try:
            return _python(version, target.path, target.name)
        except SyntaxError:
            return None
    if target.path.endswith(".md"):
        section = _section(text, target.name)
        return None if section is None else (section, " ".join(section.split()))
    if target.path.endswith(CPP_SUFFIXES):
        body = _cpp(text, target.name)
        return None if body is None else ("", "") if body == "" else (body, _without_comments(body))
    return ("", "") if re.search(rf"\b{re.escape(target.name)}\b", text) else None


def read(target: Target, files: set[str]) -> str | None:
    """The target's digest in the working tree: "" for one checked only to exist, None when it resolves to nothing."""
    found = locate(target, Version(frozenset(files)))
    return None if found is None else ("" if found == ("", "") else _digest(found[1]))


def change(target: Target, pinned_at: str, files: set[str]) -> str:
    """The target's source as a unified diff from its pin to the working tree."""
    listed = subprocess.run(["git", "ls-tree", "-r", "--name-only", pinned_at], cwd=REPO.root, capture_output=True,
                            text=True).stdout.splitlines()
    before = locate(target, Version(frozenset(listed), pinned_at))
    after = locate(target, Version(frozenset(files)))
    old, new = ("" if x is None else x[0] for x in (before, after))
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"{target} @ {pinned_at}",
                                        f"{target} (working tree)"))


# ------------------------------------------------------------------------------------------------ the lock

#: the interpreter the digests are taken under: `ast.unparse`'s output moves between Python versions, so a pin read under
#: another one reads as drift where nothing moved
PYTHON = f"{sys.version_info[0]}.{sys.version_info[1]}"


def _load(blob: str) -> dict[tuple[str, str], dict]:
    lock = json.loads(blob)
    if lock.get("python", PYTHON) != PYTHON:
        raise SystemExit(f"docrefs: {REPO.lock} was pinned under Python {lock['python']} and this is {PYTHON}, whose "
                         f"ast.unparse digests differ; run it as the repository's target does (`bazel run {REPO.label}`)")
    return {(e["doc"], e["target"]): e for e in lock["references"]}


def _dump(entries: dict[tuple[str, str], dict]) -> None:
    rows = [entries[k] for k in sorted(entries)]
    path = REPO.root / REPO.lock
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{{\n \"python\": \"{PYTHON}\",\n \"references\": [\n"
                    + ",\n".join("  " + json.dumps(r, sort_keys=True) for r in rows) + "\n ]\n}\n")


def _lock() -> dict[tuple[str, str], dict] | None:
    path = REPO.root / REPO.lock
    return _load(path.read_text()) if path.is_file() else None


def _head_sha() -> str:
    return subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], cwd=REPO.root, capture_output=True, text=True,
                          check=True).stdout.strip()


def survey(files: set[str], lock: dict | None) -> dict[tuple[str, str], tuple[Target, int, str | None]]:
    """``(doc, target) -> (target, line, digest)`` over every doc in the tree."""
    out = {}
    for doc in docs(files):
        pinned = frozenset(target for d, target in (lock or {}) if d == doc)
        for target, line in references(doc, files, pinned).items():
            out[(doc, str(target))] = (target, line, read(target, files))
    return out


def verdict(found, lock):
    """``(new, stale, broken, unread, pending)``: references the lock lacks, pins no doc makes, references that resolve
    to nothing, pinned references no docs pass has read, and pinned references whose target's digest moved (a vanished
    target reads None)."""
    new = {k: v for k, v in found.items() if k not in lock}
    stale = sorted(k for k in lock if k not in found)
    broken = sorted(k for k in found.keys() & lock.keys() if found[k][2] is None and lock[k]["sha256"] is None)
    unread = sorted(k for k in found.keys() & lock.keys() if not lock[k]["reviewed"])
    pending = {k: (found[k], lock[k]) for k in found.keys() & lock.keys() if found[k][2] != lock[k]["sha256"]}
    return new, stale, broken, unread, pending


def _pin(target: Target, digest: str | None, sha: str, doc: str, reviewed: bool) -> dict:
    return {"doc": doc, "target": str(target), "sha256": digest, "pinned_at": sha, "reviewed": reviewed}


def move(spelled: list[str], files: set[str], lock: dict[tuple[str, str], dict]) -> int:
    """A TARGET MOVED (a file, a directory, every path under a prefix): each doc's citation of `OLD...` becomes `NEW...`
    and its pin follows, read as it was read -- the pins of the primitives and sections in a moved file with it --
    refused, and nothing written, unless every moved target resolves at its new path to the digest its pin holds, so a
    move re-pins only content that is the same."""
    if len(spelled) != 2:
        raise SystemExit("docrefs move: name the old and the new path (`move OLD NEW`)")
    old, new = spelled
    moved, refused = {}, []
    for (doc, target), pin in sorted(lock.items()):
        before = Target.spelled(target)
        if not (before.path == old or before.path.startswith(old + "/")):
            continue
        after = Target(new + before.path[len(old):], before.name)
        digest = read(after, files)
        if digest is None or digest != pin["sha256"]:
            refused.append(f"  {doc}: `{target}` -> `{after}` reads {digest!r}, its pin {pin['sha256']!r}")
            continue
        moved[(doc, target)] = {**pin, "target": str(after)}
    if refused:
        print("docrefs move: these targets are not the same content at the new path; nothing is moved:")
        print("\n".join(refused))
        return 1
    texts = {doc: (REPO.root / doc).read_text() for doc in docs(files)}
    for doc, text in texts.items():
        if old in text:
            (REPO.root / doc).write_text(text.replace(old, new))
    for key, pin in moved.items():
        del lock[key]
        lock[(pin["doc"], pin["target"])] = pin
    _dump(lock)
    print(f"docrefs: moved {len(moved)} pin(s) from `{old}` to `{new}`, in {sum(old in t for t in texts.values())} doc(s)")
    return 0


def _pairs(spelled: list[str], flag: str) -> dict[str, str]:
    pairs = {}
    for item in spelled:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise SystemExit(f"docrefs: {flag} takes KEY=VALUE, not {item!r}")
        pairs[key] = value
    return pairs


def configure(argv: list[str]) -> argparse.Namespace:
    """The command line: the command and its arguments, and the repository's configuration, which becomes `REPO` (its
    root the checkout the working directory is in)."""
    global REPO
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="check", choices=("check", "pending", "accept", "prune", "init", "move"))
    ap.add_argument("docs", nargs="*", help="accept: the docs whose references were read; move: OLD NEW")
    ap.add_argument("--whole", action="store_true",
                    help="accept: every reference in the doc was read, not only the new and changed ones")
    ap.add_argument("--lock", required=True, help="the lock's path in the repository")
    ap.add_argument("--package", action="append", default=[], help="NAME=DIR: a package root and the directory it "
                                                                    "imports from (empty for the root)")
    ap.add_argument("--mirror", action="append", default=[], help="DOCS=SOURCES: a doc under DOCS mirrors the source "
                                                                  "under SOURCES at its stem")
    ap.add_argument("--exclude", action="append", default=[], help="a doc never read (a ledger of past trees)")
    ap.add_argument("--data", type=Path, help="the pinned data's root file: `data:` citations resolve in its directory")
    ap.add_argument("--label", default="//:docrefs", help="the target the messages name")
    a = ap.parse_intermixed_args(argv)
    root = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if root.returncode:
        raise SystemExit("docrefs runs inside the repository's checkout")
    REPO = Repo(root=Path(root.stdout.strip()), lock=a.lock, packages=_pairs(a.package, "--package"),
                mirrors=_pairs(a.mirror, "--mirror"), exclude=tuple(a.exclude),
                data=a.data.parent if a.data else None, label=a.label)
    return a


def main(argv: list[str] | None = None) -> int:
    a = configure(sys.argv[1:] if argv is None else argv)
    files = tracked()
    found = survey(files, _lock())
    run = f"bazel run {REPO.label} --"

    if a.command == "init":
        if _lock() is not None:
            raise SystemExit(f"docrefs: {REPO.lock} exists; the lock is initialized once")
        sha = _head_sha()
        _dump({k: _pin(t, d, sha, k[0], False) for k, (t, _, d) in found.items()})
        broken = sum(1 for _, _, d in found.values() if d is None)
        print(f"docrefs: pinned {len(found)} reference(s) unread, {broken} of them broken")
        return 0
    lock = _lock()
    if lock is None:
        raise SystemExit(f"docrefs: no lock ({run} init records one)")

    if a.command == "accept":
        if not a.docs:
            raise SystemExit("docrefs accept: name the docs whose references you read")
        sha, refused, dropped = _head_sha(), [], []
        for doc in a.docs:
            doc = str(Path(doc).resolve().relative_to(REPO.root)) if Path(doc).exists() else doc
            for key, (target, line, digest) in sorted((k, v) for k, v in found.items() if k[0] == doc):
                pin = lock.get(key)
                if digest is None:
                    if pin is not None and pin["sha256"] is not None:
                        #: the target left and the sentence was read: a bare name stops being a citation, and a reference
                        #: of any other form shows again as one that resolves to nothing, which the gate fails
                        del lock[key]
                        dropped.append(f"  {doc}:{line}: `{target}` names nothing now; its pin is dropped")
                    else:
                        refused.append(f"  {doc}:{line}: `{target}` resolves to nothing")
                    continue
                if pin is None or pin["sha256"] != digest or (a.whole and not pin["reviewed"]):
                    lock[key] = _pin(target, digest, sha, doc, True)
        _dump(lock)
        if dropped:
            print("\n".join(dropped))
        if refused:
            print("docrefs accept: these references stay unpinned until they name something that exists:")
            print("\n".join(refused))
            return 1
        print(f"docrefs: accepted {', '.join(a.docs)}")
        return 0

    if a.command == "move":
        return move(a.docs, files, lock)

    if a.command == "prune":
        gone = [k for k in lock if k not in found]
        for key in gone:
            del lock[key]
        _dump(lock)
        print(f"docrefs: pruned {len(gone)} pin(s) no doc makes any more")
        return 0

    new, stale, broken, unread, pending = verdict(found, lock)

    if a.command == "pending":
        by_target: dict[Target, list] = {}
        for (doc, _), ((target, line, digest), pin) in sorted(pending.items()):
            by_target.setdefault(target, []).append((doc, line, digest, pin["pinned_at"]))
        for target, cites in sorted(by_target.items()):
            print(f"{target}: {'VANISHED' if cites[0][2] is None else 'CHANGED'} since {cites[0][3]}")
            for doc, line, _, _ in cites:
                print(f"  cited by {doc}:{line}")
            print(change(target, cites[0][3], files))
        print(f"docrefs: {len(by_target)} target(s) pending across {len(pending)} reference(s)")
        return 0

    for (doc, target), (_, line, digest) in sorted(new.items()):
        why = "resolves to nothing" if digest is None else "is not pinned: read it against its target, then `accept`"
        print(f"{doc}:{line}: the reference to `{target}` {why}")
    for doc, target in stale:
        print(f"{doc}: the pin for `{target}` names a reference the doc no longer makes (`prune`)")
    for key in broken:
        doc, target = key
        print(f"{doc}:{found[key][1]}: `{target}` resolves to nothing: fix the sentence")
    for key in sorted(pending):
        (_, line, digest), _ = pending[key]
        print(f"{key[0]}:{line}: `{key[1]}` {'vanished' if digest is None else 'changed'} since its pin: read the sentence "
              f"against the target (`{run} pending` shows the change), fix it if it no longer holds")
    for key in unread:
        print(f"{key[0]}:{found[key][1]}: `{key[1]}` was never read against its target")
    docs_to_read = sorted({k[0] for k in pending} | {k[0] for k in new if found[k][2] is not None})
    whole = sorted({k[0] for k in unread})
    if docs_to_read:
        print(f"after reading: {run} accept " + " ".join(docs_to_read))
    if whole:
        print(f"after reading each reference of the whole doc: {run} accept --whole " + " ".join(whole))
    if pending or new or unread or broken:
        print(f"then stage `{REPO.lock}` (it moves with the docs).")
    failed = bool(new or stale or broken or unread or pending)
    print(f"docrefs: {'FAIL' if failed else 'OK'} -- {len(found)} reference(s), {len(pending)} pending, {len(unread)} unread, "
          f"{len(broken)} broken")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
