"""The machine's disk (fsla_devtools.disk), on throwaway git repositories with linked worktrees and a fake output user
root: the cap counts every repository's live worktrees together and its refusal names the clean, merged ones oldest
first; a removal expunges the output base before the checkout, refuses a dirty, unmerged or main checkout unless forced
by name (a main one never); the floor refuses under a fake free-space reader, naming each filesystem and what the
worktree tool would free, and its override says why."""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from fsla_devtools import config
from fsla_devtools.disk import downloads, floor, worktrees
from fsla_devtools.graph import front_door

GIB = 1024**3
IDENTITY = ["-c", "user.name=t", "-c", "user.email=t@example.org"]


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *IDENTITY, *args], check=True, capture_output=True,
                          text=True).stdout.strip()


class Machine(unittest.TestCase):
    """A temporary machine: two repositories beside each other, an output user root, a lock directory, its config."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.a, self.b = self.repo("alpha"), self.repo("beta")
        self.outputs = self.root / "outputs"
        self.outputs.mkdir()
        self.host({})
        self._saved = os.environ.get(config.POINTER)
        os.environ[config.POINTER] = str(self.root / "config")
        config.reload()

    def tearDown(self):
        if self._saved is None:
            os.environ.pop(config.POINTER, None)
        else:
            os.environ[config.POINTER] = self._saved
        config.reload()
        self._tmp.cleanup()

    def host(self, keys: dict) -> None:
        (self.root / "config").mkdir(exist_ok=True)
        (self.root / "config" / "host.json").write_text(json.dumps({
            "repositories": [str(self.a), str(self.b)], "worktree_cap": 3, "lock_dir": str(self.root / "locks"),
            "bazel_output_roots": [str(self.outputs)], **keys}))
        config.reload()

    def repo(self, name: str) -> Path:
        path = self.root / name
        path.mkdir()
        git(path, "init", "-q", "-b", "master")
        (path / "README").write_text("r\n")
        git(path, "add", "-A")
        git(path, "commit", "-q", "-m", "c")
        return path

    def worktree(self, repo: Path, name: str, age_days: float = 0) -> Path:
        path = self.root / "worktrees" / name
        git(repo, "worktree", "add", "-q", "--detach", str(path), "master")
        when = 1_700_000_000 - age_days * 86400
        index = Path(git(path, "rev-parse", "--absolute-git-dir")) / "index"
        os.utime(index, (when, when))
        return path

    def output_base(self, workspace: Path, name: str) -> Path:
        base = self.outputs / name
        (base / "execroot" / "out").mkdir(parents=True)
        (base / worktrees.MARKER).write_text(f"{workspace}\n")
        (base / "execroot" / "out" / "lib.so").write_bytes(b"x" * 4096)
        #: Bazel's outputs are read-only
        os.chmod(base / "execroot" / "out", 0o555)
        return base

    def fake_expunge(self, workspace: Path) -> None:
        self.expunged.append(workspace)
        for base in worktrees.output_bases(workspace):
            for d, _dirs, _files in os.walk(base):
                os.chmod(d, 0o755)
            shutil.rmtree(base)


class Cap(Machine):
    def test_the_cap_counts_every_repository_s_worktrees_together_and_names_the_clean_merged_ones_oldest_first(self):
        old = self.worktree(self.a, "old", 30)
        dirty = self.worktree(self.b, "dirty", 40)
        (dirty / "scratch.txt").write_text("untracked\n")
        new = self.worktree(self.b, "new", 2)
        with self.assertRaises(SystemExit) as refused:
            worktrees.add(self.a, self.root / "worktrees" / "fourth", "master")
        text = str(refused.exception)
        self.assertIn("holds 3 live linked worktrees", text)
        self.assertIn("cap of 3", text)
        self.assertIn(worktrees.REMOVE, text)
        self.assertLess(text.index(str(old)), text.index(str(new)))
        self.assertNotIn(str(dirty), text)
        self.assertFalse((self.root / "worktrees" / "fourth").exists())

    def test_an_unmerged_worktree_is_not_named_and_under_the_cap_an_add_creates(self):
        ahead = self.worktree(self.a, "ahead", 10)
        git(ahead, "commit", "-q", "--allow-empty", "-m", "ahead")
        made = worktrees.add(self.b, self.root / "worktrees" / "made", "master", branch="topic")
        self.assertEqual(git(made, "branch", "--show-current"), "topic")
        states = {t.path.name: t for t in worktrees.census()}
        self.assertFalse(states["ahead"].merged)
        self.assertTrue(states["made"].removable)
        self.worktree(self.a, "third", 1)
        with self.assertRaises(SystemExit) as refused:
            worktrees.add(self.a, self.root / "worktrees" / "fourth", "master")
        self.assertNotIn(str(ahead), str(refused.exception))

    def test_a_folder_gone_is_not_live_and_the_main_checkouts_never_count(self):
        gone = self.worktree(self.a, "gone")
        shutil.rmtree(gone)
        self.assertEqual(worktrees.census(), [])

    def test_an_add_in_a_repository_the_machine_does_not_name_is_refused(self):
        other = self.repo("other")
        with self.assertRaises(SystemExit) as refused:
            worktrees.add(other, self.root / "worktrees" / "x", "master")
        self.assertIn("host.repositories", str(refused.exception))

    def test_unnamed_the_repositories_are_the_checkouts_beside_whose_origin_is_an_fsla_repository(self):
        beside = self.root / "beside"
        beside.mkdir()
        for folder, url in (("fsla-standalone", "https://example.org/owner/fsla.git"),
                            ("fsla-bench", "git@example.org:owner/fsla-bench.git"),
                            ("zoology", "https://example.org/other/zoology.git"), ("loose", None)):
            git(beside, "init", "-q", folder)
            if url:
                git(beside / folder, "remote", "add", "origin", url)
        (beside / "notes").mkdir()
        self.assertEqual([p.name for p in worktrees.discover(beside)], ["fsla-bench", "fsla-standalone"])


class Remove(Machine):
    def setUp(self):
        super().setUp()
        self.expunged: list[Path] = []

    def test_a_clean_merged_worktree_goes_with_its_output_base_expunged_first(self):
        tree = self.worktree(self.a, "done")
        base = self.output_base(tree, "base1")
        other = self.output_base(self.a, "base2")
        with redirect_stdout(io.StringIO()):
            worktrees.remove(tree, expunge=self.fake_expunge)
        self.assertEqual(self.expunged, [tree])
        self.assertFalse(tree.exists() or base.exists())
        self.assertTrue(other.exists())
        self.assertNotIn(str(tree), git(self.a, "worktree", "list"))

    def test_run_in_a_worktree_with_no_argument_it_removes_that_worktree(self):
        tree = self.worktree(self.a, "here")
        with redirect_stdout(io.StringIO()):
            worktrees.remove(tree, named=False, expunge=self.fake_expunge)
        self.assertFalse(tree.exists())
        self.assertEqual(self.expunged, [], "a worktree never built has no output base to expunge")

    def test_a_dirty_or_unmerged_worktree_is_refused_unless_named_with_force(self):
        dirty = self.worktree(self.a, "dirty")
        (dirty / "README").write_text("changed\n")
        ahead = self.worktree(self.b, "ahead")
        git(ahead, "commit", "-q", "--allow-empty", "-m", "ahead")
        for tree, why in ((dirty, "changes or untracked files"), (ahead, "not merged into master")):
            with self.assertRaises(SystemExit) as refused:
                worktrees.remove(tree, expunge=self.fake_expunge)
            self.assertIn(why, str(refused.exception))
            self.assertIn("--force", str(refused.exception))
            with self.assertRaises(SystemExit):
                worktrees.remove(tree, force=True, named=False, expunge=self.fake_expunge)
            self.assertTrue(tree.exists())
        self.output_base(dirty, "base")
        with redirect_stdout(io.StringIO()):
            worktrees.remove(dirty, force=True, expunge=self.fake_expunge)
            worktrees.remove(ahead, force=True, expunge=self.fake_expunge)
        self.assertFalse(dirty.exists() or ahead.exists())
        self.assertEqual(self.expunged, [dirty])

    def test_a_main_checkout_is_never_removed_even_forced(self):
        with self.assertRaises(SystemExit) as refused:
            worktrees.remove(self.a, force=True, expunge=self.fake_expunge)
        self.assertIn("main checkout", str(refused.exception))
        self.assertTrue((self.a / "README").exists())

    def test_an_output_base_the_expunge_leaves_keeps_the_worktree(self):
        tree = self.worktree(self.a, "kept")
        self.output_base(tree, "stubborn")
        with self.assertRaises(SystemExit) as refused:
            worktrees.remove(tree, expunge=lambda w: None)
        self.assertIn("still there", str(refused.exception))
        self.assertTrue(tree.exists())

    def test_the_front_door_s_flag_is_the_tool_and_names_this_checkout(self):
        tree = self.worktree(self.a, "door")
        out = io.StringIO()
        with redirect_stdout(out):
            front_door.main(["--worktree", "list"], self.a)
        self.assertIn(str(tree), out.getvalue())
        self.assertIn("1 live linked worktrees, cap 3", out.getvalue())


class Floor(Machine):
    def readings(self, **free_gb: float):
        by_path = {Path(p): int(g * GIB) for p, g in free_gb.items()}
        return lambda path: by_path[Path(path)]

    def test_under_the_floor_a_build_is_refused_naming_each_filesystem_and_what_the_worktree_tool_frees(self):
        self.host({"watched_filesystems": [str(self.root / "wsl"), str(self.root / "c")], "free_floor_gb": 30})
        tree = self.worktree(self.a, "done", 5)
        self.output_base(tree, "base")
        free = self.readings(**{str(self.root / "wsl"): 12.5, str(self.root / "c"): 80})
        with self.assertRaises(SystemExit) as refused:
            floor.hold("build", free=free)
        text = str(refused.exception)
        self.assertIn("refusing bazel build", text)
        self.assertIn("30 GiB (host.free_floor_gb)", text)
        self.assertIn(f"{self.root / 'wsl'}", text)
        self.assertIn("12.5 GiB free  BELOW THE FLOOR", text)
        self.assertIn("80.0 GiB free", text)
        self.assertIn(str(tree), text)
        self.assertIn("1 clean, merged worktrees", text)
        self.assertIn('DISK_FLOOR_OVERRIDE="<why>"', text)

    def test_above_the_floor_nothing_is_refused_and_the_override_says_why(self):
        self.host({"watched_filesystems": ["/"], "free_floor_gb": 30})
        floor.hold("test", free=lambda p: 31 * GIB)
        os.environ[floor.OVERRIDE] = "a gate run while the drive is compacted"
        try:
            err = io.StringIO()
            with redirect_stderr(err):
                floor.hold("test", free=lambda p: GIB)
            self.assertIn("overridden (a gate run while the drive is compacted)", err.getvalue())
        finally:
            del os.environ[floor.OVERRIDE]

    def test_unnamed_the_watched_filesystems_are_the_root_and_the_windows_drive_that_holds_system32(self):
        self.host({"windows_system32": "/mnt/q/Windows/System32"})
        found = floor.watched(ismount=lambda p: Path(p) in (Path("/"), Path("/mnt/q")))
        self.assertEqual([p for p, _ in found], [Path("/"), Path("/mnt/q")])
        self.assertIn("Windows drive", found[1][1])
        self.host({})
        self.assertEqual([p for p, _ in floor.watched()], [Path("/")])

    def test_a_windows_drive_under_the_floor_says_wsl_s_freed_space_returns_only_after_compaction(self):
        readings = [(Path("/"), "WSL's root", 100 * GIB),
                    (Path("/mnt/q"), "the Windows drive WSL's virtual disk lives on", 10 * GIB)]
        text = floor.refusal("run", readings, 30, [], 0)
        self.assertIn("returns to /mnt/q only once WSL's virtual disk is compacted", text)
        self.assertIn("would free nothing", text)

    def test_only_a_command_that_builds_is_floored(self):
        self.assertEqual(floor.FLOORED, ("build", "test", "run"))


class Downloads(unittest.TestCase):
    """The download trim: the entries whose `file` Bazel last touched before the age go, at most once a day, under a
    lock a second trimmer skips; nothing else of the cache is read."""

    NOW = 1_700_000_000.0

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "config").mkdir()
        self.write_host({})
        self._saved = os.environ.get(config.POINTER)
        os.environ[config.POINTER] = str(self.root / "config")
        config.reload()
        self.repository = self.root / "cache" / "bazel" / "repository"

    def tearDown(self):
        if self._saved is None:
            os.environ.pop(config.POINTER, None)
        else:
            os.environ[config.POINTER] = self._saved
        config.reload()
        self._tmp.cleanup()

    def write_host(self, keys: dict) -> None:
        (self.root / "config" / "host.json").write_text(json.dumps({"build_cache": str(self.root / "cache"), **keys}))
        config.reload()

    def download(self, digest: str, days: float | None, kind: str = "sha256") -> Path:
        entry = self.repository / "content_addressable" / kind / digest
        entry.mkdir(parents=True)
        (entry / f"id-{digest}").touch()
        if days is not None:
            (entry / "file").write_bytes(b"x" * 1000)
            when = self.NOW - days * 86400
            os.utime(entry / "file", (when, when))
        return entry

    def test_a_download_unused_past_the_age_goes_and_one_used_since_or_still_written_stays(self):
        old, used = self.download("aa", 31), self.download("bb", 29)
        other_kind, writing = self.download("cc", 45, "sha384"), self.download("dd", None)
        contents = self.repository / "contents" / "ee"
        contents.mkdir(parents=True)
        os.utime(contents, (0, 0))
        self.assertEqual(downloads.trim(self.repository, 30, self.NOW), (2, 2000))
        self.assertFalse(old.exists() or other_kind.exists())
        self.assertTrue(used.exists() and writing.exists() and contents.exists())

    def test_the_front_door_trims_at_most_once_a_day_and_says_what_it_deleted(self):
        self.download("aa", 40)
        err = io.StringIO()
        with redirect_stderr(err):
            downloads.trim_if_due(self.NOW)
        self.assertIn("1 downloads unused for 30 days deleted", err.getvalue())
        later = self.download("bb", 40)
        downloads.trim_if_due(self.NOW + downloads.DAY / 2)
        self.assertTrue(later.exists(), "a trim within the day is not due")
        with redirect_stderr(io.StringIO()):
            downloads.trim_if_due(self.NOW + downloads.DAY + 1)
        self.assertFalse(later.exists())

    def test_a_trim_another_holds_is_skipped_and_a_null_age_trims_nothing(self):
        import fcntl

        entry = self.download("aa", 40)
        with open(self.root / "cache" / "bazel" / ".downloads-trim.lock", "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            downloads.trim_if_due(self.NOW)
        self.assertTrue(entry.exists())
        self.write_host({"downloads_max_age_days": None})
        downloads.trim_if_due(self.NOW)
        self.assertTrue(entry.exists())


class PathsKind(unittest.TestCase):
    def test_a_paths_key_expands_each_home(self):
        key = config.Key("paths?", None, "d")
        self.assertEqual(config._check("host", "k", key, ["~/x", "/y"]), [str(Path.home() / "x"), "/y"])
        with self.assertRaises(SystemExit):
            config._check("host", "k", key, "/y")


if __name__ == "__main__":
    unittest.main()
