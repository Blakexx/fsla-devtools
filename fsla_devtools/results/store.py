"""The store: one file a sample, under the label of the target that produced it.

A sample's folder is its producing target's LABEL, `@<workspace>//<package>:<target>`, at `<workspace>/<package>/<target>/`
under the records directory; its file is named by its RUN, `<run>.json` (`run_id()`: when it ran, then eight hex digits,
so a folder sorts oldest first), and its output and parts are in the folder of the same name beside it. A sample carries
its SEMANTICS (what it is a measurement of) and their digest, the KEY, so a reader that compares groups samples by key.
A sample is written once, by an exclusive create, and never again: no lock, no rewrite."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

#: THE SAMPLE FORMAT: the envelope every sample file carries (format, label, key, semantics, run, utc, ok, provenance,
#: its output or its error). A reader refuses a format it does not know rather than guessing at it; a change to the
#: envelope is a new number.
FORMAT = 2

_SEGMENT = re.compile(r"^[A-Za-z0-9_.+-]+$")
#: A LABEL: `@<workspace>//<package>:<target>`, the workspace the repository's module name
_LABEL = re.compile(r"^@([a-z][a-z0-9._-]*)//((?:[A-Za-z0-9_.+-]+/)*[A-Za-z0-9_.+-]+)?:([A-Za-z0-9_.+-]+)$")
#: A RUN: `<YYYYMMDDTHHMMSSZ>-<token>`, a sample's file name
_RUN = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[A-Za-z0-9.-]+$")
#: A MACHINE PATH: a path inside someone's home directory -- `/home/<user>`, `/Users/<user>`, `/root` (a container's or a
#: rented box's, where the checkouts sit) or a Windows user folder. A record is read on other machines and published, so it
#: names a file by its path inside its checkout and a checkout by `checkout()` (`portable()` rewrites a message so); a
#: system path (`/opt/venv/bin/python`, `/usr/local/cuda`) is a fact of the environment and is kept. A path starts a word:
#: after a letter, a digit, `.`, `~`, `:` or another `/` it is a ratio, a relative path or a URL.
_MACHINE_PATH = re.compile(r"(?<![\w.~/:\\%-])(?:/(?:home|Users)/[A-Za-z0-9_.+@-]+|/root|/mnt/[a-z]/Users/[A-Za-z0-9_.+@ -]+|"
                           r"[A-Za-z]:[\\/]Users[\\/][A-Za-z0-9_.+@ -]+)(?:[/\\][A-Za-z0-9_.+@-]*)*")


def key(semantics: dict) -> str:
    """sha256 over the semantics, canonical JSON, the first 24 hex digits."""
    return hashlib.sha256(json.dumps(semantics, sort_keys=True).encode()).hexdigest()[:24]


def utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def run_id() -> str:
    """A run's identity: when it started (UTC, to the second) and eight random hex digits -- sortable, and the name of
    the sample it stores."""
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + os.urandom(4).hex()


def digest(path: Path | str) -> str:
    """sha256 of a file's bytes."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def backend(root: Path | str | None = None) -> Path:
    """The records directory a store reads and writes: `root`, else the machine's store -- `records/` of the checkout
    the dev config's `store.root` names (fsla_devtools.config)."""
    if root is not None:
        return Path(root)
    from fsla_devtools.config import machine

    configured = machine("store.root")
    if configured is None:
        raise SystemExit("no store on this machine: name the data repository's checkout as `root` in the dev config's "
                         "store.json, or pass the records directory (--root)")
    return Path(configured) / "records"


def portable(text: str, *roots: Path | str) -> str:
    """`text` (a traceback, a tool's message) with paths inside each checkout of `roots` made relative to it and any
    other path under the home directory spelled from `~`."""
    for root in roots:
        text = text.replace(str(Path(root).resolve()) + "/", "")
    return text.replace(str(Path.home()) + "/", "~/")


def _strings(blob: object) -> Iterator[str]:
    if isinstance(blob, str):
        yield blob
    elif isinstance(blob, dict):
        for k, v in blob.items():
            yield str(k)
            yield from _strings(v)
    elif isinstance(blob, (list, tuple)):
        for v in blob:
            yield from _strings(v)


def machine_paths(blob: object) -> list[str]:
    """The machine paths in a JSON-able value -- every string in it, keys included, read as written -- or in a text."""
    return sorted({m.group(0) for s in _strings(blob) for m in _MACHINE_PATH.finditer(s)})


def checkout(path: Path | str) -> dict:
    """A git checkout's identity: its directory's name (never its path), its branch (None when detached), HEAD, and
    `diff_sha256` -- sha256 of its tracked changes against HEAD, None when clean -- so two different uncommitted trees
    over one commit differ. A git call that fails raises: an identity it could not read is not "clean, no commit"."""
    path = Path(path)

    def git(*args: str, detached_ok: bool = False) -> str:
        done = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, check=False)
        if done.returncode == 0 or (detached_ok and done.returncode == 1 and not done.stderr):
            return done.stdout
        raise RuntimeError(f"{path.resolve().name}: `git {' '.join(args)}` failed ({done.returncode}): "
                           f"{portable(done.stderr.strip(), path)}")

    diff = git("diff", "HEAD", "--", ".")
    return {"checkout": path.resolve().name, "branch": git("symbolic-ref", "--short", "-q", "HEAD", detached_ok=True).strip()
            or None, "git_sha": git("rev-parse", "HEAD").strip(),
            "diff_sha256": hashlib.sha256(diff.encode()).hexdigest() if diff.strip() else None}


def _content(path: Path) -> object:
    """What of a stored file can name a path: its JSON, its text, or nothing for bytes that are not text."""
    try:
        text = path.read_text()
    except UnicodeDecodeError:
        return None
    return json.loads(text) if path.suffix == ".json" else text


def _uncommitted(provenance: dict) -> str | None:
    """Why a timing sample's provenance names no commit, or None: its `checkouts` is a non-empty mapping, each checkout's
    identity with its `git_sha`."""
    checkouts = provenance.get("checkouts")
    if not isinstance(checkouts, dict) or not checkouts:
        return "its provenance names no checkout"
    missing = sorted(owner for owner, ident in checkouts.items() if not (isinstance(ident, dict) and ident.get("git_sha")))
    return f"checkouts {missing} name no commit" if missing else None


def label_path(label: str) -> Path:
    """A label's folder under the records directory: `<workspace>/<package>/<target>`."""
    found = _LABEL.match(label)
    if not found or any(p in (".", "..") for p in label.replace(":", "/").split("/")):
        raise ValueError(f"{label!r} is not a target's label, `@<workspace>//<package>:<target>`")
    workspace, package, target = found.groups()
    return Path(workspace, *(package.split("/") if package else ()), target)


class Store:
    """The samples of one producing target, by its label. `root` is the records directory; the default is the
    machine's store (`backend()`)."""

    def __init__(self, label: str, root: Path | str | None = None):
        self.label = label
        self.root = backend(root)
        self.dir = self.root / label_path(label)

    def samples(self, key: str | None = None) -> list[dict]:
        """Every sample, newest first; of the semantics whose digest is `key` alone when given."""
        found = [json.loads(p.read_text()) for p in sorted(self.dir.glob("*.json"), reverse=True) if _RUN.match(p.stem)]
        return [s for s in found if key is None or s["key"] == key]

    def output(self, sample: dict) -> Path:
        return self.dir / sample["output"]

    def parts(self, sample: dict) -> Path:
        """The folder that holds a sample's output and its parts, each at its name: `<run>/`."""
        return self.dir / sample["run"]

    def put(self, semantics: dict, *, output: object = None, output_file: Path | str | None = None,
            parts: dict[str, Path | str] | None = None, error: str | None = None, wall_s: float | None = None,
            provenance: dict | None = None, run: str | None = None, timing: bool = False, unless_stored: bool = False,
            **extra) -> dict | None:
        """Write one sample and return it. A success hands either `output` (stored as JSON) or `output_file` (moved in,
        its suffix kept), and may hand `parts`: the files the same execution wrote beside its output, each moved in under
        its name (a relative path of plain names); a failure hands `error`. `provenance` is what the tool knows of where
        the sample came from (its checkouts, a stage); the host is added. `run` names the sample (`run_id()` when not
        given). `timing`: the sample is a timing, which names the commit of every checkout it ran
        (`provenance["checkouts"]`, each a `checkout()`), or it is refused. `extra` is kept on the sample as given.
        `unless_stored`: when a sample of these semantics already holds this outcome -- a success whose output has these
        bytes, or a failure with this error -- nothing is written and None is returned (a build output stored again; a
        measurement stores every run)."""
        if (error is None) == (output is None and output_file is None) or (output is not None and output_file is not None):
            raise ValueError("a sample is exactly one of output, output_file or error")
        if parts and error is not None:
            raise ValueError("a failed sample has no parts")
        if timing:
            why = _uncommitted(provenance or {})
            if why:
                raise ValueError(f"{self.label}: a timing is stored only against the commits it ran, and {why}")
        stored = [semantics, output, error, provenance, extra, sorted(parts or {})]
        files = ([output_file] if output_file is not None else []) + list((parts or {}).values())
        stored += [_content(Path(f)) for f in files]
        found = machine_paths(stored)
        if found:
            raise ValueError(f"{self.label}: a record carries no machine path, found {found}: name a file by its path "
                             "inside its checkout and a checkout by checkout(); portable() rewrites a message")
        if unless_stored and self._holds(key(semantics), output, output_file, error):
            return None
        sample = {"utc": utc(), "ok": error is None, "wall_s": None if wall_s is None else round(wall_s, 3),
                  "provenance": {"host": platform.node(), **(provenance or {})}, **extra}
        if error is not None:
            sample["error"] = error[-4000:]
        return self._write(semantics, run or run_id(), sample, output, output_file, parts, shutil.move)

    def refile(self, semantics: dict, *, run: str, at: str, provenance: dict, mark: dict,
               output_file: Path | str | None = None, parts: dict[str, Path | str] | None = None,
               error: str | None = None, extra: dict | None = None) -> dict | None:
        """A sample made from bytes kept before it, never measured here: its output and `parts` COPIED in unchanged (or,
        for a failure kept, its `error`), `at` the date the bytes themselves state, `provenance` what they state (no host
        is added: this machine ran nothing), `extra` the other fields they state, kept as given, and `mark` saying where
        they were kept -- `{"backfilled": ...}` for a file kept as written, `{"migrated": ...}` for a sample of the store's
        earlier layout -- with the machine paths what was kept carries (its bytes, and the fields it states), stated,
        never refused, since nothing is rewritten. None when the label already holds a sample of this `run`."""
        if (error is None) == (output_file is None):
            raise ValueError("a re-filed sample is exactly one of output_file or error")
        if (self.dir / f"{run}.json").exists():
            return None
        files = [*([output_file] if output_file is not None else []), *(parts or {}).values()]
        found = machine_paths(mark)
        if found:
            raise ValueError(f"{self.label}: a mark names where bytes were kept, never a machine path: found {found}")
        (kind, where), = mark.items()
        kept = machine_paths([semantics, provenance, sorted(parts or {}), error, extra, *(_content(Path(f)) for f in files)])
        sample = {"utc": at, "ok": error is None, "wall_s": None, **(extra or {}), "provenance": provenance,
                  kind: {**where, "machine_paths": kept}}
        if error is not None:
            sample["error"] = error
        return self._write(semantics, run, sample, None, output_file, parts, shutil.copyfile)

    def _write(self, semantics: dict, run: str, sample: dict, output: object, output_file: Path | str | None,
               parts: dict[str, Path | str] | None, bring) -> dict:
        """The sample `<run>.json`, its output and parts first in `<run>/`, each created exclusively: a run that exists
        is refused, never overwritten."""
        if not _RUN.match(run):
            raise ValueError(f"{self.label}: run {run!r} is not `<YYYYMMDDTHHMMSSZ>-<token>`")
        for name in parts or {}:
            if not all(_SEGMENT.match(p) and p not in (".", "..") for p in name.split("/")):
                raise ValueError(f"{self.label}: part {name!r} is not a relative path of plain names")
        folder = self.dir / run
        self.dir.mkdir(parents=True, exist_ok=True)
        sample = {"format": FORMAT, "label": self.label, "key": key(semantics), "semantics": semantics, "run": run,
                  **sample}
        if sample["ok"] or parts:
            folder.mkdir()
        if sample["ok"]:
            suffix = Path(output_file).suffix if output_file is not None else ".json"
            dest = folder / f"output{suffix}"
            if output_file is not None:
                bring(str(output_file), dest)
            else:
                dest.write_text(json.dumps(output, indent=1, sort_keys=True) + "\n")
            sample["output"] = f"{run}/{dest.name}"
        if parts:
            sample["parts"] = sorted(parts)
            for name, source in parts.items():
                dest = folder / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                bring(str(source), dest)
        with open(self.dir / f"{run}.json", "x") as fh:
            fh.write(json.dumps(sample, indent=1, sort_keys=True) + "\n")
        return sample

    def _holds(self, k: str, output: object, output_file: Path | str | None, error: str | None) -> bool:
        """Whether a sample of semantics `k` is this outcome: a failure with this error, or a success whose output has
        the bytes `put` would store."""
        if error is not None:
            return any(not s["ok"] and s.get("error") == error[-4000:] for s in self.samples(k))
        written = (Path(output_file).read_bytes() if output_file is not None
                   else (json.dumps(output, indent=1, sort_keys=True) + "\n").encode())
        return any(s["ok"] and self.output(s).read_bytes() == written for s in self.samples(k))


def labels(root: Path | str | None = None) -> list[str]:
    """Every label under a records directory that holds a sample."""
    root = backend(root)
    found = set()
    for path in root.rglob("*.json"):
        if _RUN.match(path.stem):
            rel = path.parent.relative_to(root).parts
            found.add(f"@{rel[0]}//{'/'.join(rel[1:-1])}:{rel[-1]}")
    return sorted(found)


def outputs(prefix: str, root: Path | str | None = None) -> Iterator[tuple[dict, object]]:
    """Every successful sample of the labels that start with `prefix` (a label, or `@<workspace>//<package>`), oldest
    first per label: (sample, its output -- parsed when JSON, else the output's path)."""
    root = backend(root)
    for label in labels(root):
        if not label.startswith(prefix):
            continue
        store = Store(label, root)
        for sample in reversed(store.samples()):
            if sample["ok"]:
                path = store.output(sample)
                yield sample, json.loads(path.read_text()) if path.suffix == ".json" else path


def check(root: Path | str | None = None) -> list[str]:
    """What is wrong with the stored samples: a file that is no sample, a sample whose label is not its folder or whose key
    does not recompute, a format this library does not write, a successful sample's missing output or part, a failure
    with no error, a lock or a write in progress, a sample's folder with no sample, a target's folder holding another's.
    The machine path is refused where a sample is written (`put`), not here: a stored sample is never rewritten, so its
    consistency is what is checked."""
    root = backend(root)
    problems = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root)
        if path.suffix in (".lock", ".tmp"):
            problems.append(f"{rel}: a lock or a write in progress is never in the tree")
            continue
        if path.suffix != ".json" or not _RUN.match(path.stem) or len(rel.parts) < 3:
            if not any(_RUN.match(part) and (root.joinpath(*rel.parts[:i]) / f"{part}.json").is_file()
                       for i, part in enumerate(rel.parts[:-1])):
                problems.append(f"{rel}: neither a sample (`<label path>/<run>.json`) nor in a sample's folder")
            continue
        try:
            sample = json.loads(path.read_text())
        except json.JSONDecodeError as ex:
            problems.append(f"{rel}: not JSON ({ex})")
            continue
        label = sample.get("label")
        try:
            where = label_path(label)
        except (ValueError, TypeError):
            problems.append(f"{rel}: label {label!r} is not a target's label")
            continue
        if where != rel.parent:
            problems.append(f"{rel}: label {label} is not its folder")
        if sample.get("format") != FORMAT:
            problems.append(f"{rel}: format {sample.get('format')!r} is not {FORMAT}")
        if sample.get("run") != path.stem:
            problems.append(f"{rel}: run {sample.get('run')!r} is not its name")
        if sample.get("key") != key(sample.get("semantics", {})):
            problems.append(f"{rel}: the key does not recompute from the semantics")
        if sample.get("ok") and not (path.parent / str(sample.get("output"))).is_file():
            problems.append(f"{rel}: its output {sample.get('output')} is missing")
        for part in sample.get("parts", []):
            if not (path.parent / path.stem / part).is_file():
                problems.append(f"{rel}: its part {part} is missing")
        if not sample.get("ok") and not sample.get("error"):
            problems.append(f"{rel}: a failed sample carries no error")
        for sub in path.parent.iterdir():
            if sub.is_dir() and not (path.parent / f"{sub.name}.json").is_file():
                problems.append(f"{sub.relative_to(root)}: a folder in a target's folder that is no sample's")
    return sorted(set(problems))
