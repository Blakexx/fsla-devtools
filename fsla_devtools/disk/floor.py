"""THE FREE-SPACE FLOOR: a build, test or run through the front door is refused, before Bazel starts, while a watched
filesystem has less free than `host.free_floor_gb` (30 GiB).

A build that fills a disk fails midway and can take the machine with it; under WSL the Windows drive matters as much as
WSL's own root, because WSL's virtual disk lives on it and only grows: a peak stays on the Windows drive until the disk is
compacted by hand. So the watched filesystems (`host.watched_filesystems`; null: the root and, under WSL, the drive
that holds `host.windows_system32`) are read before every build, and a refusal names each one, its free space, the
floor and what the worktree tool would free: the clean, merged worktrees and their output bases.

The one override is an environment variable that says why: `DISK_FLOOR_OVERRIDE="<why>" bazel ...`, the name the
machine's memory guard reads for its own floor, so one name means one thing on the machine.
"""
from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

from fsla_devtools import config
from fsla_devtools.disk import worktrees

GIB = 1024**3
OVERRIDE = "DISK_FLOOR_OVERRIDE"
#: the commands the floor holds: those that build, and so write the output base, the caches and the scratch
FLOORED = ("build", "test", "run")


def mount(path: Path, ismount: Callable[[Path], bool] = os.path.ismount) -> Path:
    """The mount point that holds `path`: its nearest ancestor that is one."""
    path = Path(path)
    while not ismount(path) and path != path.parent:
        path = path.parent
    return path


def watched(ismount: Callable[[Path], bool] = os.path.ismount) -> list[tuple[Path, str]]:
    """The watched filesystems as (path, what it is): `host.watched_filesystems`, else the root and, under WSL, the
    Windows drive WSL's virtual disk lives on -- the mount that holds `host.windows_system32` (Windows keeps its system
    and WSL's disk on the system drive)."""
    configured = config.machine("host.watched_filesystems")
    if configured is not None:
        return [(Path(p), "") for p in dict.fromkeys(configured)]
    system32 = config.machine("host.windows_system32")
    found = [(Path("/"), "WSL's root" if system32 else "the root")]
    if system32:
        drive = mount(Path(system32), ismount)
        if drive != Path("/"):
            found.append((drive, "the Windows drive WSL's virtual disk lives on"))
    return found


def _free(path: Path) -> int:
    return shutil.disk_usage(path).free


def refusal(command: str, readings: list[tuple[Path, str, int]], floor_gb: int, removable: list, freed: int) -> str:
    width = max(len(str(p)) for p, _label, _free in readings)
    lines = [f"refusing bazel {command}: a watched filesystem has less free space than the floor, "
             f"{floor_gb} GiB (host.free_floor_gb), and a build there can fill it:"]
    for path, label, free in readings:
        mark = "  BELOW THE FLOOR" if free < floor_gb * GIB else ""
        lines.append(f"  {str(path):<{width}}  {free / GIB:7.1f} GiB free{mark}" + (f"  ({label})" if label else ""))
    if removable:
        lines.append(f"The worktree tool would free {freed / GIB:.1f} GiB: {len(removable)} clean, merged worktrees "
                     f"with their output bases, oldest first (`{worktrees.REMOVE}` in one, or `{worktrees.REMOVE} "
                     "<path>`; `bazel --worktree list` shows every one):")
        lines += [f"  {worktrees.describe(t)}" for t in removable]
    else:
        lines.append("The worktree tool would free nothing: no worktree is both clean and merged "
                     "(`bazel --worktree list`).")
    windows = [p for p, label, free in readings if label.startswith("the Windows drive") and free < floor_gb * GIB]
    if windows:
        lines.append(f"Space freed inside WSL returns to {windows[0]} only once WSL's virtual disk is compacted.")
    lines.append(f'Override, saying why: {OVERRIDE}="<why>" bazel {command} ...')
    return "\n".join(lines)


def hold(command: str, free: Callable[[Path], int] = _free, ismount: Callable[[Path], bool] = os.path.ismount) -> None:
    """Refuse `command` (SystemExit) while a watched filesystem is under the floor, unless `DISK_FLOOR_OVERRIDE` says
    why; returns when every one keeps the floor."""
    floor_gb = config.machine("host.free_floor_gb")
    readings = [(path, label, free(path)) for path, label in watched(ismount)]
    if all(f >= floor_gb * GIB for _p, _l, f in readings):
        return
    why = os.environ.get(OVERRIDE, "").strip()
    short = ", ".join(f"{p} {f / GIB:.1f} GiB" for p, _l, f in readings if f < floor_gb * GIB)
    if why:
        print(f"free-space floor ({floor_gb} GiB) overridden ({why}): {short}", file=sys.stderr)
        return
    removable = [t for t in worktrees.census() if t.removable]
    freed = sum(worktrees.disk_bytes([t.path, *worktrees.output_bases(t.path)]) for t in removable)
    raise SystemExit(refusal(command, readings, floor_gb, removable, freed))
