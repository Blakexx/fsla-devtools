"""THE WORKTREE CAP: the machine holds at most `host.worktree_cap` live linked worktrees across its FSLA repositories
(`host.repositories`) together, and a worktree leaves with its Bazel output base.

A linked worktree costs its checkout and, once built, its Bazel output base (2-4 GB in fsla), which Bazel keeps under
the output user root after the checkout is gone. So creating one past the cap is refused, naming the ones that can go
-- clean (no tracked change, no untracked file), merged into their repository's default branch and not locked, oldest
first -- and the command that removes one; and removing one expunges its output base first (`bazel clean --expunge` in
it, which stops its server), then removes the checkout (`git worktree remove`). A removal refuses a worktree that is
dirty or unmerged unless it is named with `--force`, and never removes a main checkout.

    bazel --worktree list                          # every linked worktree on the machine, its state, the cap
    bazel --worktree add <path> <commit> [-b BR]   # a worktree of this checkout's repository, under the cap
    bazel --worktree remove                        # THIS worktree (the checkout the command runs in)
    bazel --worktree remove <path> [--force]       # another one, by name; --force removes a dirty or unmerged one

The front door runs it (`bazel --worktree ...`, its arguments after the flag), so every repository has it in each of
its checkouts and it needs no Bazel build: a removal works on a full disk. A repository's own worktree command creates
through `add` and keeps its own set-up steps after (fsla's `bazel run //:worktree`).
"""
from __future__ import annotations

import argparse
import getpass
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fsla_devtools import config

#: THE FSLA REPOSITORIES, by the name their `origin` ends in: the checkouts the cap counts when `host.repositories` is
#: null (a checkout's folder may have any name; its remote names the repository)
NAMES = ("fsla", "fsla-devtools", "warpline", "fsla-results", "fsla-bench")
#: the fsla-devtools checkout this module runs from, beside whose main checkout the repositories are found
CHECKOUT = Path(__file__).resolve().parents[2]
#: the file Bazel writes in an output base, naming the workspace it belongs to
MARKER = "DO_NOT_BUILD_HERE"
GIB = 1024**3


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)


def _out(cwd: Path, *args: str) -> str | None:
    done = _git(cwd, *args)
    return done.stdout.strip() if done.returncode == 0 else None


def repository_name(url: str) -> str:
    """The repository a remote URL names: its last path segment, without `.git` (`https://host/owner/fsla.git`,
    `git@host:owner/fsla.git` -> `fsla`)."""
    return url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1].removesuffix(".git")


def main_checkout(path: Path) -> Path | None:
    """The main checkout of the repository `path` is a checkout of (itself, or a linked worktree's), or None."""
    common = _out(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(common).parent if common else None


def discover(beside: Path) -> list[Path]:
    """The FSLA repositories' main checkouts in `beside`: every git checkout there whose `origin` is one of `NAMES`."""
    found = []
    for child in sorted(p for p in beside.iterdir() if (p / ".git").is_dir()):
        url = _out(child, "config", "--get", "remote.origin.url")
        if url and repository_name(url) in NAMES:
            found.append(child)
    return found


def repositories() -> list[Path]:
    """The machine's repositories: `host.repositories`, else those beside fsla-devtools' main checkout."""
    configured = config.machine("host.repositories")
    if configured is not None:
        return [Path(p) for p in configured]
    main = main_checkout(CHECKOUT)
    return discover(main.parent) if main else []


@dataclass(frozen=True)
class Worktree:
    path: Path
    repository: Path  #: its repository's main checkout
    head: str
    branch: str | None
    locked: bool
    clean: bool  #: no tracked change and no untracked file
    merged: bool  #: its HEAD is in its repository's default branch
    default: str  #: that branch, as the check read it ("" where the repository names none)
    touched: float  #: when git last wrote its index: its last checkout, stage or commit

    @property
    def removable(self) -> bool:
        return self.clean and self.merged and not self.locked


def default_branches(repository: Path) -> list[str]:
    """The refs that are the repository's default branch: `origin/HEAD`'s target and the local branch of its name,
    else `main` or `master`, locally and at `origin` -- those that exist."""
    target = _out(repository, "symbolic-ref", "-q", "refs/remotes/origin/HEAD")
    names = [target.removeprefix("refs/remotes/origin/")] if target else ["main", "master"]
    refs = [ref for name in names for ref in (f"refs/heads/{name}", f"refs/remotes/origin/{name}")]
    return [ref for ref in refs if _git(repository, "rev-parse", "--verify", "-q", ref).returncode == 0]


def _state(path: Path, repository: Path, head: str, branch: str | None, locked: bool, defaults: list[str]) -> Worktree:
    #: read only: no index refresh written, so the census never touches what it measures
    status = _git(path, "--no-optional-locks", "status", "--porcelain")
    merged = [ref for ref in defaults if _git(path, "merge-base", "--is-ancestor", head, ref).returncode == 0]
    gitdir = Path(_out(path, "rev-parse", "--absolute-git-dir") or path)
    index = gitdir / "index"
    return Worktree(path=path, repository=repository, head=head, branch=branch, locked=locked,
                    clean=status.returncode == 0 and not status.stdout.strip(), merged=bool(merged),
                    default=(merged or defaults or [""])[0].rsplit("/", 1)[-1],
                    touched=(index if index.exists() else gitdir).stat().st_mtime)


def linked(repository: Path) -> list[Worktree]:
    """The repository's live linked worktrees: every one `git worktree list` names but its main checkout and those whose
    folder is gone (git's `prunable`)."""
    listing = _out(repository, "worktree", "list", "--porcelain")
    if listing is None:
        raise SystemExit(f"{repository}: not a git checkout (host.repositories names it)")
    entries = [dict(line.split(" ", 1) if " " in line else (line, "") for line in block.splitlines())
               for block in listing.split("\n\n") if block.strip()]
    defaults = default_branches(repository)
    trees = []
    for entry in entries[1:]:
        path = Path(entry["worktree"])
        if "prunable" in entry or "bare" in entry or not path.is_dir():
            continue
        branch = entry.get("branch", "").removeprefix("refs/heads/") or None
        trees.append(_state(path, repository, entry.get("HEAD", ""), branch, "locked" in entry, defaults))
    return trees


def census() -> list[Worktree]:
    """Every live linked worktree of the machine's repositories, oldest first."""
    return sorted((t for repository in repositories() for t in linked(repository)), key=lambda t: t.touched)


def output_roots() -> list[Path]:
    """The Bazel output user roots: `host.bazel_output_roots`, else Bazel's default for this user."""
    configured = config.machine("host.bazel_output_roots")
    if configured is not None:
        return [Path(p) for p in configured]
    return [Path.home() / ".cache" / "bazel" / f"_bazel_{getpass.getuser()}"]


def output_bases(workspace: Path) -> list[Path]:
    """The output bases whose `DO_NOT_BUILD_HERE` names `workspace`, in every output user root."""
    names = {str(workspace), str(workspace.resolve())}
    found = []
    for root in output_roots():
        for base in sorted(root.iterdir()) if root.is_dir() else []:
            marker = base / MARKER
            try:
                if marker.is_file() and marker.read_text().strip() in names:
                    found.append(base)
            except OSError:
                continue
    return found


def disk_bytes(paths: list[Path]) -> int:
    """The bytes `paths` hold on disk together, a hardlinked file once and no link followed (`du`)."""
    paths = [p for p in paths if p.exists()]
    if not paths:
        return 0
    done = subprocess.run(["du", "-s", "-c", "-B1", *map(str, paths)], capture_output=True, text=True)
    lines = done.stdout.strip().splitlines()
    return int(lines[-1].split()[0]) if lines else 0


def _when(t: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(t))


def describe(tree: Worktree) -> str:
    state = [("clean" if tree.clean else "dirty"),
             (f"merged into {tree.default}" if tree.merged else f"not merged into {tree.default or 'a default branch'}")]
    if tree.locked:
        state.append("locked")
    return f"{_when(tree.touched)}  {tree.path}  ({tree.repository.name}: {', '.join(state)})"


REMOVE = "bazel --worktree remove"


def cap_refusal(live: list[Worktree], cap: int) -> str:
    removable = [t for t in live if t.removable]
    lines = [f"refusing: the machine holds {len(live)} live linked worktrees across its repositories, at its cap of {cap} "
             f"(host.worktree_cap)."]
    if removable:
        lines.append(f"The clean, merged ones, oldest first -- remove one with `{REMOVE}` run in it, or "
                     f"`{REMOVE} <path>` from any checkout:")
        lines += [f"  {describe(t)}" for t in removable]
    else:
        lines.append("None is both clean and merged: `bazel --worktree list` shows each one's state; "
                     f"`{REMOVE} --force <path>` removes a dirty or unmerged one, its changes lost.")
    return "\n".join(lines)


def add(workspace: Path, path: Path, commit: str, branch: str | None = None) -> Path:
    """A linked worktree of `workspace`'s repository at `path`, checked out at `commit` (on a new `branch`, or detached),
    refused past the cap; the census and the add hold the machine's `worktrees` lock, so two adds never both pass."""
    from fsla_devtools.locks import host

    repository = main_checkout(workspace)
    if repository is None:
        raise SystemExit(f"refusing: {workspace} is not a git checkout")
    known = {p.resolve() for p in repositories()}
    if repository.resolve() not in known:
        raise SystemExit(f"refusing: {repository} is not one of the machine's repositories, whose worktrees the cap "
                         f"counts ({sorted(map(str, known))}); host.repositories names it")
    with host.file_lock("worktrees", label=f"worktree add {path}"):
        live = census()
        cap = config.machine("host.worktree_cap")
        if len(live) >= cap:
            raise SystemExit(cap_refusal(live, cap))
        subprocess.run(["git", "-C", str(repository), "worktree", "add", *(["-b", branch] if branch else []),
                        str(path), commit], check=True)
    return path


def expunge(workspace: Path) -> None:
    """`bazel clean --expunge` in the workspace: Bazel removes the output base it owns, its read-only outputs among
    them, and stops the server that holds it. The Bazel is the one bazelisk handed the front door (`BAZEL_REAL`)."""
    bazel = os.environ.get("BAZEL_REAL")
    if not bazel:
        raise SystemExit("refusing: no Bazel to expunge the output base with -- run it as `bazel --worktree remove`, "
                         "through the front door")
    subprocess.run([bazel, "clean", "--expunge"], cwd=workspace, check=True)


def remove(path: Path, *, force: bool = False, named: bool = True,
           expunge: Callable[[Path], None] = expunge) -> None:
    """Remove the linked worktree at `path`: its Bazel output base expunged first, then the checkout. Refuses a main
    checkout always, a locked worktree, and a dirty or unmerged one unless `force` -- which needs the worktree named."""
    if force and not named:
        raise SystemExit("refusing: --force removes only a worktree named by its path: "
                         f"`{REMOVE} --force <path>`")
    top = _out(path, "rev-parse", "--show-toplevel")
    if top is None or Path(top).resolve() != path.resolve():
        raise SystemExit(f"refusing: {path} is not the root of a git checkout" + (f" (its root is {top})" if top else ""))
    if _out(path, "rev-parse", "--absolute-git-dir") == _out(path, "rev-parse", "--path-format=absolute",
                                                             "--git-common-dir"):
        raise SystemExit(f"refusing: {path} is a main checkout, which is never removed")
    repository = main_checkout(path)
    tree = next((t for t in linked(repository) if t.path.resolve() == path.resolve()), None)
    if tree is None:
        raise SystemExit(f"refusing: {path} is not a live linked worktree of {repository}")
    if tree.locked:
        raise SystemExit(f"refusing: {path} is locked: `git worktree unlock {path}` first")
    problems = ([] if tree.clean else ["it has changes or untracked files"]) + (
        [] if tree.merged else [f"its HEAD {tree.head[:12]} is not merged into {tree.default or 'a default branch'}"])
    if problems and not force:
        raise SystemExit(f"refusing: {path}: {'; '.join(problems)}. `{REMOVE} --force {path}` removes it anyway, "
                         "and what is not merged is lost")
    bases = output_bases(path)
    if bases:
        expunge(path)
        left = [b for b in bases if b.exists()]
        if left:
            raise SystemExit(f"refusing to remove {path}: its output base is still there after the expunge: {left}")
    subprocess.run(["git", "-C", str(repository), "worktree", "remove", *(["--force"] if force else []), str(path)],
                   check=True)
    gone = f", its output base {', '.join(map(str, bases))} expunged" if bases else ""
    print(f"removed {path}{gone}" + (f" ({'; '.join(problems)})" if problems else ""))


def main(argv: list[str], workspace: Path) -> int:
    """`bazel --worktree <list|add|remove>`, for the checkout at `workspace` (the front door's)."""
    ap = argparse.ArgumentParser(prog="bazel --worktree", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="action", required=True)
    sub.add_parser("list", help="every live linked worktree on the machine, oldest first, and the cap")
    p = sub.add_parser("add", help="a linked worktree of this checkout's repository, under the cap")
    p.add_argument("path", type=Path)
    p.add_argument("commit")
    p.add_argument("-b", dest="branch", help="a new branch to check out at the commit (default: detached)")
    p = sub.add_parser("remove", help="this worktree, or the one named: its output base expunged, then the checkout")
    p.add_argument("path", nargs="?", type=Path, help="the worktree to remove (default: this one)")
    p.add_argument("--force", action="store_true", help="remove a dirty or unmerged worktree, named by its path")
    a = ap.parse_args(argv)
    if a.action == "list":
        live = census()
        for tree in live:
            print(describe(tree))
        print(f"{len(live)} live linked worktrees, cap {config.machine('host.worktree_cap')} (host.worktree_cap); "
              f"{sum(t.removable for t in live)} clean and merged")
        return 0
    if a.action == "add":
        add(workspace, Path.cwd() / a.path, a.commit, a.branch)
        return 0
    remove(Path.cwd() / a.path if a.path else workspace, force=a.force, named=a.path is not None)
    return 0
