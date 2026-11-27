"""The store: records under a location, keyed by their semantics, every sample kept."""
from __future__ import annotations

import fcntl
import hashlib
import json
import platform
import re
import shutil
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

#: THE RECORD FORMAT: the envelope every record carries (key, location, format, semantics, samples). A reader refuses a
#: format it does not know rather than guessing at it; a change to the envelope is a new number.
FORMAT = 1

_SEGMENT = re.compile(r"^[A-Za-z0-9_.-]+$")
_RECORD = re.compile(r"^[0-9a-f]{24}\.json$")
#: A MACHINE PATH: a path inside someone's home directory -- `/home/<user>`, `/Users/<user>`, `/root` (a container's or a
#: rented box's, where the checkouts sit) or a Windows user folder. A record is read on other machines and published, so it
#: names a file by its path inside its checkout and a checkout by `checkout()` (`portable()` rewrites a message so); a
#: system path (`/opt/venv/bin/python`, `/usr/local/cuda`) is a fact of the environment and is kept. A path starts a word:
#: after a letter, a digit, `.`, `~`, `:` or another `/` it is a ratio, a relative path or a URL.
_MACHINE_PATH = re.compile(r"(?<![\w.~/:\\%-])(?:/(?:home|Users)/[A-Za-z0-9_.+@-]+|/root|/mnt/[a-z]/Users/[A-Za-z0-9_.+@ -]+|"
                           r"[A-Za-z]:[\\/]Users[\\/][A-Za-z0-9_.+@ -]+)(?:[/\\][A-Za-z0-9_.+@-]*)*")
#: THE LOCATIONS OF A TIMING: a timing is comparable only to the code it ran, so a sample stored here names a commit
TIMING = "timing"


def key(semantics: dict) -> str:
    """sha256 over the semantics, canonical JSON, the first 24 hex digits."""
    return hashlib.sha256(json.dumps(semantics, sort_keys=True).encode()).hexdigest()[:24]


def utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


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


def _timing(location: str) -> bool:
    return location == TIMING or location.startswith(TIMING + "/")


def _uncommitted(provenance: dict) -> str | None:
    """Why a timing sample's provenance names no commit, or None: its `checkouts` is a non-empty mapping, each checkout's
    identity with its `git_sha`."""
    checkouts = provenance.get("checkouts")
    if not isinstance(checkouts, dict) or not checkouts:
        return "its provenance names no checkout"
    missing = sorted(owner for owner, ident in checkouts.items() if not (isinstance(ident, dict) and ident.get("git_sha")))
    return f"checkouts {missing} name no commit" if missing else None


class Store:
    """The records of one location. `root` is the backend's directory; the default is the machine's store
    (`backend()`)."""

    def __init__(self, location: str, root: Path | str | None = None):
        parts = location.split("/")
        if not location or not all(_SEGMENT.match(p) and p not in (".", "..") for p in parts):
            raise ValueError(f"location {location!r} is not a relative path of plain names")
        self.location = location
        self.root = backend(root)
        self.dir = self.root / location

    def path(self, k: str) -> Path:
        return self.dir / f"{k}.json"

    def get(self, k: str) -> dict | None:
        path = self.path(k)
        return json.loads(path.read_text()) if path.exists() else None

    @staticmethod
    def complete(record: dict | None) -> bool:
        """A record is complete when one of its samples succeeded."""
        return bool(record) and any(s["ok"] for s in record["samples"])

    def output(self, record: dict, n: int | None = None) -> Path:
        """The output of sample `n`, or of the latest sample that succeeded."""
        ok = [s for s in record["samples"] if s["ok"] and (n is None or s["n"] == n)]
        if not ok:
            raise LookupError(f"{self.location}/{record['key']} has no successful sample" + (f" {n}" if n is not None else ""))
        return self.dir / ok[-1]["output"]

    def records(self) -> Iterator[dict]:
        for path in sorted(self.dir.glob("*.json")):
            if _RECORD.match(path.name):
                yield json.loads(path.read_text())

    def put(self, semantics: dict, *, output: object = None, output_file: Path | str | None = None,
            error: str | None = None, wall_s: float | None = None, provenance: dict | None = None,
            unless_stored: bool = False, **extra) -> dict | None:
        """Add one sample under the semantics' key and return it. A success hands either `output` (stored as JSON) or
        `output_file` (moved in, its suffix kept); a failure hands `error`. `provenance` is what the tool knows of where
        the sample came from (its checkouts, a stage); the host is added. `extra` is kept on the sample as given. A sample
        at a timing location (`timing/...`) names the commit of every checkout it ran (`provenance["checkouts"]`, each
        a `checkout()`), or it is refused. `unless_stored`: when the record already holds this outcome -- a successful
        sample whose output has these bytes, or a failed one with this error -- nothing is stored and None is returned
        (a build output stored again; a measurement stores every run)."""
        if (error is None) == (output is None and output_file is None) or (output is not None and output_file is not None):
            raise ValueError("a sample is exactly one of output, output_file or error")
        if _timing(self.location):
            why = _uncommitted(provenance or {})
            if why:
                raise ValueError(f"{self.location}: a timing is stored only against the commits it ran, and {why}")
        stored = [semantics, output, error, provenance, extra]
        if output_file is not None:
            stored.append(_content(Path(output_file)))
        found = machine_paths(stored)
        if found:
            raise ValueError(f"{self.location}: a record carries no machine path, found {found}: name a file by its path "
                             "inside its checkout and a checkout by checkout(); portable() rewrites a message")
        k = key(semantics)
        self.dir.mkdir(parents=True, exist_ok=True)
        with self._locked(k):
            record = self.get(k) or {"key": k, "location": self.location, "format": FORMAT, "semantics": semantics,
                                     "samples": []}
            if record.get("format") != FORMAT:
                raise RuntimeError(f"{self.location}/{k} is format {record.get('format')}, this library writes {FORMAT}")
            if record["semantics"] != semantics:
                raise RuntimeError(f"{self.location}/{k}: stored semantics differ from the ones given (a key collision)")
            if unless_stored and self._holds(record, output, output_file, error):
                return None
            n = len(record["samples"])
            sample = {"n": n, "ok": error is None, "utc": utc(), "wall_s": None if wall_s is None else round(wall_s, 3),
                      "provenance": {"host": platform.node(), **(provenance or {})}, **extra}
            if error is not None:
                sample["error"] = error[-4000:]
            else:
                suffix = Path(output_file).suffix if output_file is not None else ".json"
                dest = self.dir / f"{k}.{n}.out{suffix}"
                if output_file is not None:
                    shutil.move(str(output_file), dest)
                else:
                    dest.write_text(json.dumps(output, indent=1, sort_keys=True) + "\n")
                sample["output"] = dest.name
            record["samples"].append(sample)
            tmp = self.path(k).with_suffix(".tmp")
            tmp.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
            tmp.replace(self.path(k))
        return sample

    def _holds(self, record: dict, output: object, output_file: Path | str | None, error: str | None) -> bool:
        """Whether a sample of `record` is this outcome: a failure with this error, or a success whose output has the
        bytes `put` would store."""
        if error is not None:
            return any(not s["ok"] and s.get("error") == error[-4000:] for s in record["samples"])
        written = (Path(output_file).read_bytes() if output_file is not None
                   else (json.dumps(output, indent=1, sort_keys=True) + "\n").encode())
        return any(s["ok"] and (self.dir / s["output"]).read_bytes() == written for s in record["samples"])

    @contextmanager
    def _locked(self, k: str) -> Iterator[None]:
        with open(self.dir / f"{k}.lock", "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)


def locations(root: Path | str | None = None) -> list[str]:
    """Every location under a backend that holds a record."""
    root = backend(root)
    return sorted({str(p.parent.relative_to(root)) for p in root.rglob("*.json") if _RECORD.match(p.name)})


def outputs(prefix: str, root: Path | str | None = None) -> Iterator[tuple[dict, dict, object]]:
    """Every successful sample under the locations `prefix` names (the location itself or any below it), oldest first
    per record: (record, sample, its output -- parsed when JSON, else the output's path)."""
    root = backend(root)
    for loc in locations(root):
        if loc != prefix and not loc.startswith(prefix.rstrip("/") + "/"):
            continue
        store = Store(loc, root)
        for record in store.records():
            for sample in record["samples"]:
                if sample["ok"]:
                    path = store.dir / sample["output"]
                    yield record, sample, json.loads(path.read_text()) if path.suffix == ".json" else path


def check(root: Path | str | None = None) -> list[str]:
    """What is wrong with the stored records: a key that does not recompute, a location that is not the record's
    directory, samples out of order, a successful sample whose output is missing, a failed one with no error. The machine
    path is refused where a sample is written (`put`), not here: a stored record is never rewritten, so its consistency
    is what is checked."""
    root = backend(root)
    problems = []
    for loc in locations(root):
        for path in sorted((root / loc).glob("*.json")):
            if not _RECORD.match(path.name):
                continue
            where = f"{loc}/{path.name}"
            try:
                record = json.loads(path.read_text())
            except json.JSONDecodeError as ex:
                problems.append(f"{where}: not JSON ({ex})")
                continue
            if record.get("key") != path.stem or key(record.get("semantics", {})) != path.stem:
                problems.append(f"{where}: the key does not recompute from the semantics")
            if record.get("location") != loc:
                problems.append(f"{where}: location {record.get('location')!r} is not its directory")
            if record.get("format") != FORMAT:
                problems.append(f"{where}: format {record.get('format')!r} is not {FORMAT}")
            for i, sample in enumerate(record.get("samples", [])):
                if sample.get("n") != i:
                    problems.append(f"{where}: sample {i} is numbered {sample.get('n')}")
                out = root / loc / str(sample.get("output"))
                if sample.get("ok") and not out.is_file():
                    problems.append(f"{where}: sample {i}'s output {sample.get('output')} is missing")
                if not sample.get("ok") and not sample.get("error"):
                    problems.append(f"{where}: failed sample {i} carries no error")
    return problems


def check_files(directory: Path | str) -> list[str]:
    """What is wrong with a directory of files kept as written (the lab's records, `lab/`): a JSON file that does not
    parse, a CSV file that does not, a file that is not text."""
    directory = Path(directory)
    problems = []
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        where = str(path.relative_to(directory))
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            problems.append(f"{where}: not text")
            continue
        if path.suffix == ".json":
            try:
                json.loads(text)
            except json.JSONDecodeError as ex:
                problems.append(f"{where}: not JSON ({ex})")
                continue
        elif path.suffix == ".csv":
            import csv

            try:
                for _row in csv.reader(text.splitlines(), strict=True):
                    pass
            except csv.Error as ex:
                problems.append(f"{where}: not CSV ({ex})")
                continue
    return problems
