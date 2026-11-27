"""THE DEV CONFIG READER: every value that depends on the machine, read from one directory of JSON files.

The directory is `~/.config/fsla/`, or the directory `FSLA_DEV_CONFIG` names (a container, a test). It holds one file a
section. A SCHEMA declares each section's keys with their type, default and meaning: a key a file holds that its
section does not declare is refused, so a typo fails loudly instead of silently taking a default, and a missing file or
key takes the declared default.

`MACHINE` is the part every FSLA repository shares -- the host's locks and budget (`host`), its clock (`clock`) and
the measurements store (`store`) -- and `machine(key)` reads it; the locks read nothing else. A repository with
sections of its own reads its whole schema with `read(schema, owns_directory=True)`, which also refuses a file no
section declares (fsla's `tools/dev_config.py`).

A setting is never read from the process environment; `POINTER` is the one variable read here, and it names a
directory, never a value.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

POINTER = "FSLA_DEV_CONFIG"


@dataclass(frozen=True)
class Key:
    kind: str  #: path, paths (a list of them), str, int, float, bool or argv; a trailing ? admits null
    default: Any
    doc: str


MACHINE: dict[str, dict[str, Key]] = {
    "host": {
        "nvcc_threads": Key("int", 2, "nvcc -t for each compile job"),
        "build_jobs": Key("int?", None, "ninja jobs a build asks for; null derives it from nvcc_threads and memory"),
        "budget_slots": Key("int?", None, "the machine-wide compute slots every build and census compile shares; "
                            "null derives cores - 2 bounded by memory (fsla_devtools.locks.host)"),
        #: THE ONE LOCK PATH: the machine's locks in one directory of their own, the same path on every machine -- a
        #: container's launcher mounts its host's there, so the two serialize on one set of files with no detection
        "lock_dir": Key("path", "/tmp/fsla-locks", "where the machine's locks live (the host budget's slots, the file "
                        "locks, the GPU lock beside them): one path on every machine, a container's mounted from its host"),
        "gpu_lock": Key("path", "/tmp/fsla-locks/fsla_gpu.lock", "the GPU lock file, in the lock directory, so one mount "
                        "shares every lock"),
        "gpu_shared_slots": Key("int", 2, "correctness runs that may hold the GPU lock shared at once"),
        "nice": Key("bool", True, "a shared GPU holder (a correctness run) and a command under the host budget's CLI "
                    "lower their own CPU and IO priority; a measurement never does"),
        "locks_trace": Key("bool", False, "print every lock acquire and release to stderr"),
        "scratch": Key("path", "/tmp/fsla", "the tools' scratch root; never read back as a record"),
        "build_cache": Key("path", "~/.cache/fsla/build", "the build system's cache of completed targets and run "
                           "workspaces (fsla_devtools.build); wipeable"),
        "store_max_gb": Key("int", 20, "the environment store's size bound in GiB (uv's wheel cache and the venvs built "
                            "from it, beside the build cache's `bazel/disk`, which fsla's rc bounds): each environment build "
                            "sweeps the wheels and environments no live environment holds, oldest first, "
                            "down to it"),
        "store_max_age_days": Key("int", 30, "an environment or cached wheel idle this many days is swept"),
        "tools_dir": Key("path", "~/.local/share/fsla/tools", "where fsla's `tools/dev.py init` installs pinned tools"),
        #: THE MACHINE'S DISK (fsla_devtools.disk): a cap on live worktrees, and a free-space floor under every build
        "repositories": Key("paths?", None, "the main checkouts of the machine's FSLA repositories, whose linked "
                            "worktrees the worktree cap counts together; null: every git checkout beside fsla-devtools' "
                            "own main checkout whose `origin` is an FSLA repository (fsla_devtools.disk.worktrees.NAMES)"),
        "worktree_cap": Key("int", 16, "the live linked worktrees the machine holds across `repositories`: creating one "
                            "past it is refused, naming the clean, merged ones to remove"),
        "bazel_output_roots": Key("paths?", None, "the Bazel output user roots the machine's checkouts build under, where "
                                  "a worktree's output base is found (its `DO_NOT_BUILD_HERE` names the workspace); "
                                  "null: Bazel's default, `~/.cache/bazel/_bazel_<user>`"),
        "free_floor_gb": Key("int", 30, "the free space in GiB each watched filesystem keeps: a build, test or run "
                             "through the front door is refused, before Bazel starts, while one has less"),
        "downloads_max_age_days": Key("int?", 30, "a download in Bazel's repository cache (`build_cache`'s "
                                      "`bazel/repository/content_addressable`) unused this many days is deleted by the "
                                      "front door, at most once a day; each is refetched by its pinned sha256 when a "
                                      "fetch needs it. Null: none is deleted"),
        "watched_filesystems": Key("paths?", None, "paths whose filesystems the free-space floor watches; null: the root "
                                   "and, under WSL, the Windows drive its virtual disk lives on (the mount that holds "
                                   "`windows_system32`)"),
        "windows_system32": Key("path?", None, "WSL only: the Windows System32 directory as mounted in Linux"),
        "wsl_lib": Key("path?", None, "WSL only: the Windows driver's Linux libraries (libdxcore, libcuda); the dev "
                       "container mounts their directory's parent read-only (the loader opens the driver store beside "
                       "lib/), without which CUDA fails to initialize inside it"),
    },
    "clock": {
        "ghz": Key("float?", None, "the SM clock the harness locks, in GHz; null means this host runs unlocked"),
        "lock": Key("argv?", None, "the command that locks the clock"),
        "unlock": Key("argv?", None, "the command that releases it"),
    },
    "store": {
        "root": Key("path?", None, "the measurements store: a checkout of the data repository (fsla-results), its "
                    "`records/` the store's backend (fsla_devtools.results), shared by every worktree on the machine; "
                    "null means no store, and a store opened without a directory of its own refuses"),
    },
}


def directory() -> Path:
    pointer = os.environ.get(POINTER)
    return Path(pointer).expanduser() if pointer else Path.home() / ".config" / "fsla"


def _check(section: str, name: str, key: Key, value: Any) -> Any:
    kind, optional = key.kind.rstrip("?"), key.kind.endswith("?")
    where = f"{directory() / (section + '.json')}: {name}"
    if value is None:
        if optional:
            return None
        raise SystemExit(f"{where} may not be null ({key.doc})")
    strings = isinstance(value, list) and all(isinstance(x, str) for x in value)
    ok = {"path": isinstance(value, str), "str": isinstance(value, str), "int": isinstance(value, int)
          and not isinstance(value, bool), "float": isinstance(value, (int, float)) and not isinstance(value, bool),
          "bool": isinstance(value, bool), "argv": strings, "paths": strings}
    if not ok[kind]:
        raise SystemExit(f"{where} must be {kind}, got {value!r} ({key.doc})")
    if kind == "paths":
        return [str(Path(v).expanduser()) for v in value]
    return str(Path(value).expanduser()) if kind == "path" else value


def read(schema: dict[str, dict[str, Key]], *, owns_directory: bool) -> dict[str, dict[str, tuple[Any, str]]]:
    """Every section's every key as `(value, source)`: the file it was read from, or "default". `owns_directory`: the
    schema declares every file the directory may hold, so an undeclared one is refused."""
    root = directory()
    if owns_directory and root.exists():
        stray = sorted(p.name for p in root.glob("*.json") if p.stem not in schema)
        if stray:
            raise SystemExit(f"{root}: undeclared config file(s) {stray}; declared: {sorted(schema)}")
    out: dict[str, dict[str, tuple[Any, str]]] = {}
    for section, keys in schema.items():
        path = root / f"{section}.json"
        doc = json.loads(path.read_text()) if path.exists() else {}
        unknown = sorted(set(doc) - set(keys))
        if unknown:
            raise SystemExit(f"{path}: undeclared key(s) {unknown}; declared: {sorted(keys)}")
        out[section] = {name: (_check(section, name, key, doc[name]), str(path)) if name in doc
                        else (_check(section, name, key, key.default), "default") for name, key in keys.items()}
    return out


@cache
def _machine() -> dict[str, dict[str, tuple[Any, str]]]:
    return read(MACHINE, owns_directory=False)


def machine(dotted: str) -> Any:
    """One value of the shared machine sections (`host.gpu_lock`, `clock.ghz`)."""
    section, name = dotted.split(".", 1)
    return _machine()[section][name][0]


def reload() -> None:
    """Forget what was read: a process that repoints `FSLA_DEV_CONFIG` reads the new directory next."""
    _machine.cache_clear()


__all__ = ["MACHINE", "POINTER", "Key", "directory", "machine", "read", "reload"]
