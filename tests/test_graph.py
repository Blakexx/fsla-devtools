"""The build graph's shared machinery (`rola_devtools.graph`): the front door overrides a workspace's `rola_devtools` pin
with this checkout only where the workspace depends on the module, since Bazel refuses an override of a module the
graph does not hold; and the environment's store key follows a local package's content, its leftovers aside."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from rola_devtools import config
from rola_devtools.graph import front_door, make_env


class FrontDoor(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        (root / "config").mkdir()
        (root / "config" / "host.json").write_text(f'{{"build_cache": "{root / "cache"}"}}')
        self._saved = os.environ.get(config.POINTER)
        os.environ[config.POINTER] = str(root / "config")
        config.reload()
        self.root = root

    def tearDown(self):
        if self._saved is None:
            os.environ.pop(config.POINTER, None)
        else:
            os.environ[config.POINTER] = self._saved
        config.reload()
        self._tmp.cleanup()

    def _workspace(self, module: str) -> Path:
        workspace = self.root / "workspace"
        workspace.mkdir(exist_ok=True)
        (workspace / "MODULE.bazel").write_text(module)
        return workspace

    def test_a_workspace_that_depends_on_the_module_gets_this_checkout_as_its_override(self):
        workspace = self._workspace('module(name = "w")\nbazel_dep(name = "rola_devtools", version = "0.1.0")\n')
        rc = front_door.workspace_rc(workspace)
        self.assertIsNotNone(rc)
        self.assertIn(f"--override_module=rola_devtools={front_door.CHECKOUT}", rc.read_text())

    def test_a_workspace_that_does_not_gets_no_override(self):
        self.assertIsNone(front_door.workspace_rc(self._workspace('module(name = "w")\nbazel_dep(name = "other")\n')))

    def test_the_machine_rc_names_the_machine_s_caches(self):
        text = front_door.machine_rc().read_text()
        self.assertIn(f"--disk_cache={self.root / 'cache' / 'bazel' / 'disk'}", text)

    def test_the_machine_rc_names_the_store_s_bound(self):
        (self.root / "config" / "host.json").write_text(
            f'{{"build_cache": "{self.root / "cache"}", "store_max_gb": 3, "store_max_age_days": 7}}')
        config.reload()
        text = front_door.machine_rc().read_text()
        self.assertIn(f"--repo_env=ROLA_GRAPH_STORE_MAX_BYTES={3 * 1024**3}", text)
        self.assertIn("--repo_env=ROLA_GRAPH_STORE_MAX_AGE_DAYS=7", text)


class StoreSweep(unittest.TestCase):
    """The environment store's bound: size and age, what a live environment holds kept, the asked-for one never swept."""

    NOW = 1_000_000_000.0

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = Path(self._tmp.name)
        (self.store / "venvs").mkdir()
        (self.store / "uv-cache" / "archive-v0").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _age(self, path: Path, days: float) -> None:
        when = self.NOW - days * make_env.DAY
        os.utime(path, (when, when))

    def _wheel(self, name: str, days: float, size: int = 1000) -> Path:
        entry = self.store / "uv-cache" / "archive-v0" / name
        entry.mkdir()
        (entry / "lib.so").write_bytes(b"x" * size)
        self._age(entry / "lib.so", days)
        self._age(entry, days)
        return entry

    def _env(self, key: str, days: float, holds: tuple[Path, ...] = ()) -> Path:
        env = self.store / "venvs" / key
        env.mkdir()
        (env / ".complete").write_text("uv\n")
        for entry in holds:
            os.link(entry / "lib.so", env / f"{entry.name}.so")
        (env / make_env.USED).touch()
        self._age(env / make_env.USED, days)
        return env

    def test_an_idle_environment_and_a_wheel_no_one_holds_go_by_age_and_what_is_held_stays(self):
        by_old, by_live, loose = self._wheel("by_old", 90), self._wheel("by_live", 90), self._wheel("loose", 90)
        recent = self._wheel("recent", 1)
        keep, old = self._env("keep", 0), self._env("old", 90, (by_old,))
        live = self._env("live", 2, (by_live,))
        out = make_env.sweep(self.store, keep, None, 30, self.NOW)
        self.assertEqual(out["environments"], ["old"])
        #: the wheel the swept environment held is no one's once it is gone, so it goes with the one never held
        self.assertEqual(sorted(out["wheels"]), ["by_old", "loose"])
        self.assertTrue(keep.exists() and live.exists() and recent.exists() and by_live.exists())
        self.assertFalse(old.exists() or by_old.exists() or loose.exists())

    def test_over_its_size_the_store_sheds_the_oldest_wheels_until_it_fits(self):
        for name, days in (("a", 5), ("b", 20), ("c", 10)):
            self._wheel(name, days)
        out = make_env.sweep(self.store, self.store / "venvs" / "none", 2500, None, self.NOW)
        self.assertEqual(out["wheels"], ["b"])
        self.assertEqual(out["bytes"], 2000)
        self.assertEqual(sorted(p.name for p in (self.store / "uv-cache" / "archive-v0").iterdir()), ["a", "c"])

    def test_when_every_wheel_is_held_the_oldest_idle_environment_goes_then_its_wheels_and_the_asked_for_one_never(self):
        w1, w2, w3 = self._wheel("w1", 1), self._wheel("w2", 1), self._wheel("w3", 1)
        keep = self._env("keep", 0, (w3,))
        old, mid = self._env("old", 50, (w1,)), self._env("mid", 20, (w2,))
        out = make_env.sweep(self.store, keep, 1500, None, self.NOW)
        self.assertEqual(out["environments"], ["old", "mid"])
        self.assertEqual(sorted(out["wheels"]), ["w1", "w2"])
        self.assertTrue(keep.exists() and w3.exists())
        self.assertFalse(old.exists() or mid.exists())

    def test_the_bound_is_a_floor_of_the_asked_for_environment_s_own_size(self):
        keep = self._env("keep", 0, (self._wheel("w", 1, 5000),))
        out = make_env.sweep(self.store, keep, 10, None, self.NOW)
        self.assertEqual(out["environments"] + out["wheels"], [])
        self.assertGreater(out["bytes"], 10)

    def test_an_environment_built_before_the_bound_starts_its_idle_clock_at_the_first_sweep(self):
        env = self.store / "venvs" / "legacy"
        env.mkdir()
        (env / ".complete").write_text("uv\n")
        self._age(env / ".complete", 400)
        out = make_env.sweep(self.store, self.store / "venvs" / "none", None, 30, self.NOW)
        self.assertEqual(out["environments"], [])
        self.assertEqual((env / make_env.USED).stat().st_mtime, self.NOW)


class StoreKey(unittest.TestCase):
    def test_a_local_package_s_key_follows_its_content_and_not_its_leftovers(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            (package / "m.py").write_text("x = 1\n")
            first = make_env._identity(str(package))
            (package / "__pycache__").mkdir()
            (package / "__pycache__" / "m.cpython-311.pyc").write_bytes(b"\0")
            self.assertEqual(make_env._identity(str(package)), first)
            (package / "m.py").write_text("x = 2\n")
            self.assertNotEqual(make_env._identity(str(package)), first)

    def test_building_against_the_environment_is_another_environment(self):
        pin = "rola-zoology @ git+https://github.com/Blakexx/rola-zoology@ea219fde"
        isolated = make_env._key(b"lock", [pin], "3.11.13", "uv 0.9", "", True)
        self.assertEqual(make_env._key(b"lock", [pin], "3.11.13", "uv 0.9", "", True), isolated)
        self.assertNotEqual(make_env._key(b"lock", [pin], "3.11.13", "uv 0.9", "", False), isolated)

    def test_a_requirement_s_key_is_its_text(self):
        pin = "zoology @ git+https://github.com/HazyResearch/zoology@0123456789abcdef0123456789abcdef01234567"
        self.assertEqual(make_env._identity(pin), pin.encode())


if __name__ == "__main__":
    unittest.main()
