"""THE DOWNLOAD TRIM: the front door deletes the downloads in Bazel's repository cache (`host.build_cache`'s
`bazel/repository/content_addressable`, the machine's one `--repository_cache`) unused for `host.downloads_max_age_days`
(30), the only thing it deletes from a cache.

Bazel keeps every archive a repository rule downloads there, by the sha256 the rule pins it to, and deletes none; the
repo contents cache beside it (`contents/`) is Bazel's own to collect (`--repo_contents_cache_gc_max_age`). A download
is SAFE TO DELETE: the next fetch that needs it downloads it again and checks it against the same pinned digest --
deleting one costs a download, never a different result. "Unused" is read from the entry's `file`:
Bazel 9.3 touches its modification time on every cache hit (a download behind a live repo contents cache entry is never
hit, so it ages while that entry serves the repository; a refetch needs it only once the entry is gone too).

Cheap when nothing is due: the front door checks a stamp's time on every invocation and trims at most once a day
(`EVERY`), under a lock a second trimmer does not wait for. Null `host.downloads_max_age_days` trims nothing.
"""
from __future__ import annotations

import fcntl
import os
import shutil
import sys
import time
from pathlib import Path

from fsla_devtools import config

DAY = 86400
#: how often the front door looks: a download's age is counted in days
EVERY = DAY
GIB = 1024**3


def _bazel() -> Path:
    return Path(config.machine("host.build_cache")) / "bazel"


def stale(repository: Path, max_age_days: float, now: float) -> list[tuple[Path, int]]:
    """The downloads (`content_addressable/<digest kind>/<digest>/`, each an entry holding `file`) whose `file` was last
    used before `max_age_days` ago, with its size. An entry with no `file` yet is a write in progress, and stays."""
    limit = now - max_age_days * DAY
    found = []
    for entry in sorted((repository / "content_addressable").glob("*/*")):
        try:
            st = (entry / "file").stat()
        except OSError:
            continue
        if st.st_mtime < limit:
            found.append((entry, st.st_size))
    return found


def trim(repository: Path, max_age_days: float, now: float | None = None) -> tuple[int, int]:
    """Delete the stale downloads; returns how many went and their bytes."""
    gone = stale(repository, max_age_days, time.time() if now is None else now)
    for entry, _size in gone:
        shutil.rmtree(entry, ignore_errors=True)
    return len(gone), sum(size for _entry, size in gone)


def trim_if_due(now: float | None = None) -> None:
    """The front door's trim: at most once an `EVERY`, under a lock nobody waits for, one line when it deleted any."""
    age = config.machine("host.downloads_max_age_days")
    bazel = _bazel()
    repository = bazel / "repository"
    stamp = bazel / "downloads-trimmed.stamp"
    now = time.time() if now is None else now
    if age is None or not repository.is_dir():
        return
    if stamp.is_file() and stamp.stat().st_mtime > now - EVERY:
        return
    with open(bazel / ".downloads-trim.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        if stamp.is_file() and stamp.stat().st_mtime > now - EVERY:
            return
        count, freed = trim(repository, age, now)
        stamp.touch()
        os.utime(stamp, (now, now))
    if count:
        print(f"download trim: {count} downloads unused for {age} days deleted from {repository} "
              f"({freed / GIB:.2f} GiB; each is refetched by its pinned digest when a fetch needs it)", file=sys.stderr)
