"""THE TIMING SYSTEM: interleaved timing, as the session program a Bazel run target starts (`session`, over
`bazel/measure.bzl`'s `timing_entry` and `exported_entry` targets): a session, its null gate and the memory pass.

An ENTRY EXECUTOR, in the bundle, is `executor(cell, params) -> Timed`: `call()` runs one launch and returns its elapsed
milliseconds by the stopwatch `instrument` names; `built` describes what it built; `reset()`, when the launch changes
what the next call reads (a carried state, a decode step), restores exactly what the first call saw, untimed;
`outside_allocator()` returns device bytes its framework's allocator does not see. Every call does the same work on the
same data. An entry that cannot set up (a kernel this binary lacks) or that fails a call is a DOMAIN failure: recorded in
the session with its error and dropped from it, the rest timed. A clock read off the lock, two stopwatches in one
session, a worker that dies, or a crash of the timing itself fails the run.
"""
from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


def interpreter(root: Path, venv: str) -> str:
    """A bundle's interpreter: `venv` is its environment's completion mark by its path under `root` (an entry
    description's `bundle.venv`, a runfiles path), whose resolved directory is the store venv -- so a description names
    no machine path and the run finds the venv wherever it runs."""
    return str(Path(os.path.realpath(root / venv)).parent / "bin" / "python3")


@dataclass(frozen=True)
class Timed:
    call: Callable[[], float]
    built: dict
    instrument: str
    reset: Callable[[], None] | None = None
    outside_allocator: Callable[[], int] | None = None


__all__ = ["Timed", "interpreter"]
