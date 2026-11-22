"""THE PUBLIC MIRROR'S EXPORT: the committed files a repository publishes, written to a directory, refused when a
tracked path has no declaration or anything private-shaped would leave.

    python -m fsla_devtools.mirror EXPORT_DIR   write HEAD's export; exit 1 on any refusal
    python -m fsla_devtools.mirror --check      every tracked path is declared; exit 1 naming the ones that are not
    python -m fsla_devtools.mirror --public     print the public repository (owner/name) the export is pushed to

Run from inside the repository. Its own part is `.github/mirror/declarations.json`; see this package's README.
"""
from __future__ import annotations

import io
import json
import re
import subprocess
import sys
import tarfile
from pathlib import Path

#: where a repository declares what it publishes, relative to its root
DECLARATIONS = Path(".github") / "mirror" / "declarations.json"

#: agent and scratch files, refused at any depth. `.claude/` and `CLAUDE.md` are refused only where the repository's
#: declarations keep them private (`agent_files`, on by default: the paper keeps it on); `SCRATCHPAD.md` is always refused
AGENT_FILES = re.compile(r"(^|/)(\.claude/|CLAUDE\.md$)")
SCRATCH_FILES = re.compile(r"(^|/)SCRATCHPAD\.md$")
#: private-shaped strings. Each is spelled so that this file's own text does not match it: the tool is scanned with
#: everything else where it ships.
_OCTET = r"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])"
PRIVATE_SHAPES = {
    "a home directory": re.compile(r"/home/[a-z]|/Users/[A-Za-z]"),
    "the private scratch repository": re.compile(r"rola-scr[a]tch"),
    "a personal email address": re.compile(r"(?<![\w.+-])(?!git@)[\w.+-]+@(?!users[.]noreply[.]github[.]com|example[.])"
                                           r"[A-Za-z0-9-]+(?:[.][A-Za-z0-9-]+)*[.][A-Za-z]{2,}"),
    "an IPv4 address": re.compile(rf"(?<![\w.=§-])(?<![^/]/){_OCTET}(?:[.]{_OCTET}){{3}}(?![\w-]|[.][0-9])"),
    "a cloud project or bucket": re.compile(r"project-[0-9a-f]{8}-[0-9a-f]{4}|cla-zoology-sw[e]ep"),
    "a provider account or instance id": re.compile(r"arn:aws[a-z-]*:[a-z0-9-]*:[a-z0-9-]*:[0-9]{12}:|\bi-[0-9a-f]{17}\b"
                                                    r"|\bami-[0-9a-f]{17}\b"),
    "a ledger event id": re.compile(r"event:20[0-9]{2}-[0-9]{2}-[0-9]{2}-"),
    "a credential": re.compile(r"ghp_[A-Za-z0-9]{20}|github_pat_[A-Za-z0-9_]{20}|hf_[A-Za-z0-9]{30}|sk-ant-[a-z0-9]"
                               r"|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY"),
}
#: shapes no declaration may allow
NEVER_ALLOWED = ("a credential",)
#: addresses that are not a person's: loopback and the documentation ranges
_NOT_AN_ADDRESS = re.compile(r"^(?:0[.]0[.]0[.]0|127[.].*|192[.]0[.]2[.].*|198[.]51[.]100[.].*|203[.]0[.]113[.].*)$")
#: the optional declarations: `agent_files` (bool, default true: `.claude/` and `CLAUDE.md` stay private) and `emails`
#: (the addresses a published file may carry: the author's)
OPTIONAL = {"agent_files": True, "emails": []}


def root() -> Path:
    """The repository the working directory is inside."""
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if out.returncode:
        raise SystemExit("fsla_devtools.mirror runs inside a git repository")
    return Path(out.stdout.strip())


def declarations(path: Path | None = None) -> dict:
    path = path or root() / DECLARATIONS
    blob = json.loads(path.read_text())
    required = {"public", "ships", "private", "allow"}
    if not required <= set(blob) or set(blob) - required - set(OPTIONAL):
        raise SystemExit(f"{path}: fields must be public, ships, private, allow and optionally {sorted(OPTIONAL)}; "
                         f"got {sorted(blob)}")
    for key, default in OPTIONAL.items():
        blob.setdefault(key, default)
    unknown = set(blob["allow"]) - set(PRIVATE_SHAPES)
    forbidden = set(blob["allow"]) & set(NEVER_ALLOWED)
    if unknown or forbidden:
        raise SystemExit(f"{path}: `allow` names {sorted(unknown | forbidden)}, which is no allowable shape")
    return blob


def declaration(path: str, decl: dict) -> str | None:
    """"ships" or "private" by the longest entry `path` falls under; None when it falls under none."""
    matches = [(len(entry), kind) for kind in ("ships", "private") for entry in decl[kind]
               if path == entry or path.startswith(entry.rstrip("/") + "/")]
    return max(matches)[1] if matches else None


def _git(*args: str) -> bytes:
    return subprocess.run(["git", "-C", str(root()), *args], capture_output=True, check=True).stdout


def undeclared(paths: list[str], decl: dict) -> list[str]:
    return [p for p in paths if declaration(p, decl) is None]


def scan(name: str, lines: list[tuple[int, str]], decl: dict, numbered: bool = True) -> list[str]:
    """The refusals for one file's `name` and its `lines` (line number, text; a diff's added lines are enough): the
    agent-file rule where the declarations keep agent files private, then each private-shaped string the declarations do
    not allow, at its first line."""
    if SCRATCH_FILES.search(name) or (decl["agent_files"] and AGENT_FILES.search(name)):
        return [f"{name}: an agent file"]
    findings = []
    for shape, pattern in PRIVATE_SHAPES.items():
        if shape in decl["allow"]:
            continue
        for number, line in lines:
            if any(not _allowed(shape, m.group(), decl) for m in pattern.finditer(line)):
                findings.append(f"{name}:{number}: {shape}" if numbered else f"{name}: {shape}")
                break
    return findings


def _allowed(shape: str, found: str, decl: dict) -> bool:
    if shape == "a personal email address":
        return found in decl["emails"]
    return shape == "an IPv4 address" and bool(_NOT_AN_ADDRESS.match(found))


def whole(text: str) -> list[tuple[int, str]]:
    return list(enumerate(text.splitlines(), 1))


def scan_files(files: dict[str, list[tuple[int, str]]], decl: dict, numbered: bool = True) -> list[str]:
    """The tripwire over a set of files (path to numbered lines): the files declared private are not scanned, they do not
    ship."""
    return [f for name, lines in sorted(files.items()) if declaration(name, decl) != "private"
            for f in scan(name, lines, decl, numbered)]


def added_lines(diff: str) -> dict[str, list[tuple[int, str]]]:
    """The added lines of a unified diff (`git diff -U0`, or `git log -p -U0`), by the path they were added to, each with
    its line number in the new file. A deleted file is absent; a new or renamed path is named even when it adds no line."""
    added: dict[str, list[tuple[int, str]]] = {}
    current, number = None, 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = None if line == "+++ /dev/null" else line[6:]
            if current is not None:
                added.setdefault(current, [])
        elif line.startswith("@@"):
            number = int(re.match(r"@@ -\S+ \+(\d+)", line).group(1))
        elif line.startswith("+") and current is not None:
            added[current].append((number, line[1:]))
            number += 1
    return added


def scan_diff(diff: str, decl: dict) -> list[str]:
    """The tripwire over a diff: its added lines only, so a violation already in history is not raised again."""
    return scan_files(added_lines(diff), decl)


def staged_diff() -> str:
    return _git("diff", "--cached", "--no-color", "--no-ext-diff", "--no-renames", "-U0", "--diff-filter=AM").decode(
        errors="ignore")


def range_diff(revisions: list[str]) -> str:
    """Every commit of `a..b`, patch by patch: a secret added and removed within the range is still in the history."""
    return _git("log", "-p", "--no-color", "--no-ext-diff", "--no-renames", "-U0", "--diff-filter=AM", "--format=",
                *revisions).decode(errors="ignore")


def tripwire(export: Path, decl: dict) -> list[str]:
    """The tripwire over the files under `export`."""
    files = {p.relative_to(export).as_posix(): whole(p.read_bytes().decode("utf-8", errors="ignore"))
             for p in export.rglob("*") if p.is_file()}
    return scan_files(files, {**decl, "private": []}, numbered=False)


def export(target: Path, decl: dict) -> list[str]:
    """Write HEAD's shipping files under `target`; the refusals, empty when the export is clean."""
    paths = [p for p in _git("ls-tree", "-r", "-z", "--name-only", "HEAD").decode().split("\0") if p]
    missing = undeclared(paths, decl)
    if missing:
        return [f"{p}: tracked but declared neither ships nor private" for p in missing]
    ships = [p for p in paths if declaration(p, decl) == "ships"]
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(_git("archive", "--format=tar", "HEAD", "--", *ships))) as tar:
        tar.extractall(target, filter="data")
    return tripwire(target, decl)


def main(argv: list[str]) -> int:
    decl = declarations()
    if argv == ["--public"]:
        print(decl["public"])
        return 0
    if argv == ["--check"]:
        refusals = [f"{p}: tracked but declared neither ships nor private"
                    for p in undeclared([p for p in _git("ls-files", "-z").decode().split("\0") if p], decl)]
    elif len(argv) == 1 and not argv[0].startswith("-"):
        refusals = export(Path(argv[0]), decl)
    else:
        raise SystemExit(__doc__)
    for refusal in refusals:
        print(f"MIRROR REFUSED: {refusal} ({DECLARATIONS})")
    return 1 if refusals else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
