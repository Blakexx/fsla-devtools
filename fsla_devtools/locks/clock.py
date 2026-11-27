"""THE CLOCK LOCK SEAM (moved from fsla's `tools/clock_lock.py`): the host says how its clock is locked; the harness
only asks.

A GPU's boost governor moves the SM clock with power, and a kernel number is comparable
across runs only at one clock (fsla's `docs/internals/common/sm_clock.md`). How a clock is locked
is a fact about the HOST -- `nvidia-smi -lgc` as root on Linux, an elevated scheduled task
on a Windows host under WSL, nothing at all on a box that forbids it -- so it lives in the dev
config's `clock.json` (`fsla_devtools.config`; fsla's `python tools/dev.py clock --mhz N` writes it):

    {"ghz": 1.665,
     "lock":   ["/mnt/c/Windows/System32/schtasks.exe", "/run", "/tn", "gpu-lock"],
     "unlock": ["/mnt/c/Windows/System32/schtasks.exe", "/run", "/tn", "gpu-unlock"]}

ONE LOCK FOR THE MACHINE, COUNTED BY ITS HOLDERS. The clock is the machine's, so `held()` is too: every holder, in any
process of any repository or container sharing `host.lock_dir`, keeps a holder file there locked for as long as it holds.
The first holder runs the lock command and only the last one out runs the unlock, so one process finishing never unlocks
the clock under another's session. A holder that dies without releasing leaves its file unlocked, and the next count
drops it. The proof is the caller's: it reads the device's clock and judges the reading with `within`. Without a clock a
host runs UNLOCKED, which a caller states rather than assumes.
"""
from __future__ import annotations

import contextlib
import fcntl
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

from ..config import directory, machine

TOLERANCE = 0.01


def load() -> dict | None:
    cfg = {key: machine(f"clock.{key}") for key in ("ghz", "lock", "unlock")}
    if cfg["ghz"] is None:
        return None
    missing = [key for key in ("lock", "unlock") if not cfg[key]]
    if missing:
        raise SystemExit(f"{directory() / 'clock.json'}: ghz is set but {missing} is not")
    return cfg


def _run(cmd: list[str]) -> None:
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if done.returncode:
        raise SystemExit(f"CLOCK: {cmd[0]} failed ({done.returncode}): {(done.stderr or done.stdout).strip()[-400:]}; the "
                         "host's lock is broken -- fsla's python tools/dev.py clock --mhz N re-registers and proves it")


def within(ghz: float | None, cfg: dict | None) -> bool:
    """whether a measured clock sits on the declared lock; an unlocked run is not judged."""
    return cfg is None or (ghz is not None and abs(ghz - cfg["ghz"]) <= TOLERANCE * cfg["ghz"])


def _dir() -> Path:
    path = Path(machine("host.lock_dir")) / "fsla_clock"
    path.mkdir(parents=True, exist_ok=True)
    return path


def holders() -> int:
    """How many live holders the machine's clock lock has; a dead holder's file is removed."""
    count = 0
    for path in _dir().glob("holder.*"):
        try:
            fd = os.open(path, os.O_RDWR)
        except FileNotFoundError:
            continue
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            count += 1
        else:
            path.unlink(missing_ok=True)
        finally:
            os.close(fd)
    return count


@contextlib.contextmanager
def _admission() -> Iterator[None]:
    """Counting and engaging, or leaving and releasing, one holder at a time."""
    fd = os.open(_dir() / "admission.lock", os.O_CREAT | os.O_RDWR, 0o666)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


#: this process's holds: a nested hold opens no second holder file
_HELD: list[int] = []


@contextlib.contextmanager
def held() -> Iterator[dict | None]:
    """The host's clock lock held for the block -- engaged by the machine's first holder, released by its last -- and
    the declared lock yielded; None on a host that runs unlocked, where nothing is held."""
    cfg = load()
    if cfg is None:
        yield None
        return
    if _HELD:
        _HELD.append(_HELD[-1])
        try:
            yield cfg
        finally:
            _HELD.pop()
        return
    path = _dir() / f"holder.{os.getpid()}"
    with _admission():
        first = holders() == 0
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o666)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            if first:
                _run(cfg["lock"])
        except BaseException:
            path.unlink(missing_ok=True)
            os.close(fd)
            raise
    _HELD.append(fd)
    try:
        yield cfg
    finally:
        _HELD.pop()
        with _admission():
            path.unlink(missing_ok=True)
            os.close(fd)
            if holders() == 0:
                _run(cfg["unlock"])


__all__ = ["TOLERANCE", "held", "holders", "load", "within"]
