"""The store's contract, on a temporary backend (no GPU): one record a semantics with every sample kept, a location of
plain names, an output file moved in, a checkout's identity or a failure to read it, a timing refused without a commit,
and no machine path in anything written -- a path in someone's home directory, refused at the door, while a system path is
kept and a stored record is checked for its consistency alone. `python -m unittest tests.test_results_store`"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from fsla_devtools import config
from fsla_devtools.results import Store, backend, check, check_files, checkout, key, locations, outputs, portable

#: a home-directory prefix, joined so this file carries none
HOME = "/".join(("", "home", "someone"))
#: a rented box's container checkout: no home directory, still a path on one machine (audit C4)
BOX = "/".join(("", "root", "work", "fsla"))
COMMITTED = {"checkouts": {"tip": {"checkout": "tip", "git_sha": "a" * 40, "diff_sha256": None}}}


class StoreContract(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store("tool/sub", self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_the_same_semantics_land_on_one_record_and_every_sample_is_kept(self) -> None:
        sem = {"cell": "a", "binary": "b1"}
        self.store.put(sem, output={"ms": 1.0}, provenance={"git_sha": "x"}, wall_s=0.5)
        self.store.put(sem, error="boom")
        self.store.put(sem, output={"ms": 2.0})
        record = self.store.get(key(sem))
        self.assertEqual([s["ok"] for s in record["samples"]], [True, False, True])
        self.assertEqual(record["samples"][0]["provenance"]["git_sha"], "x")
        self.assertIn("host", record["samples"][0]["provenance"])
        self.assertEqual(json.loads(self.store.output(record).read_text()), {"ms": 2.0})
        self.assertEqual(json.loads(self.store.output(record, 0).read_text()), {"ms": 1.0})
        self.assertTrue(Store.complete(record))

    def test_a_record_of_failures_is_not_complete(self) -> None:
        sem = {"cell": "b"}
        self.store.put(sem, error="first")
        record = self.store.get(key(sem))
        self.assertFalse(Store.complete(record))
        with self.assertRaises(LookupError):
            self.store.output(record)

    def test_a_changed_input_is_a_new_record(self) -> None:
        self.store.put({"binary": "b1"}, output=1)
        self.store.put({"binary": "b2"}, output=1)
        self.assertEqual(len(list(self.store.records())), 2)

    def test_an_output_file_is_moved_in_with_its_suffix(self) -> None:
        src = self.root / "capture.csv"
        src.write_text("a,b\n")
        sample = self.store.put({"cell": "c"}, output_file=src)
        self.assertEqual(sample["output"], f"{key({'cell': 'c'})}.0.out.csv")
        self.assertFalse(src.exists())

    def test_a_sample_is_exactly_one_of_output_or_error(self) -> None:
        with self.assertRaises(ValueError):
            self.store.put({"x": 1})
        with self.assertRaises(ValueError):
            self.store.put({"x": 1}, output=1, error="e")

    def test_a_location_is_a_relative_path_of_plain_names(self) -> None:
        for bad in ("", "/abs", "a/../b", "a b", "a//b"):
            with self.assertRaises(ValueError):
                Store(bad, self.root)

    def test_outputs_walks_a_prefix_and_skips_failures(self) -> None:
        Store("mqar/a", self.root).put({"cell": "x"}, output={"acc": 1})
        Store("mqar/a", self.root).put({"cell": "y"}, error="oom")
        Store("mqar/b", self.root).put({"cell": "z"}, output={"acc": 2})
        Store("mqarx", self.root).put({"cell": "w"}, output={"acc": 3})
        self.assertEqual(sorted(o["acc"] for _r, _s, o in outputs("mqar", self.root)), [1, 2])
        self.assertEqual([o["acc"] for _r, _s, o in outputs("mqar/b", self.root)], [2])

    def test_check_finds_a_record_whose_semantics_were_edited(self) -> None:
        self.store.put({"cell": "d"}, output=1)
        self.assertEqual(check(self.root), [])
        self.assertEqual(locations(self.root), ["tool/sub"])
        path = self.store.path(key({"cell": "d"}))
        record = json.loads(path.read_text())
        record["semantics"]["cell"] = "e"
        path.write_text(json.dumps(record))
        self.assertTrue(any("does not recompute" in p for p in check(self.root)))

    def git(self, repo: Path, *args: str) -> None:
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True)

    def test_a_checkout_names_its_directory_and_digests_its_diff(self) -> None:
        repo = self.root / "repo"
        repo.mkdir()
        self.git(repo, "init", "-q")
        (repo / "f").write_text("a\n")
        self.git(repo, "add", "f")
        self.git(repo, "commit", "-qm", "c")
        clean = checkout(repo)
        self.assertEqual((clean["checkout"], clean["diff_sha256"]), ("repo", None))
        self.assertIsNotNone(clean["branch"])
        self.git(repo, "checkout", "-q", "--detach")
        self.assertIsNone(checkout(repo)["branch"])
        (repo / "f").write_text("b\n")
        one = checkout(repo)["diff_sha256"]
        (repo / "f").write_text("c\n")
        self.assertNotIn(None, (one, checkout(repo)["diff_sha256"]))
        self.assertNotEqual(one, checkout(repo)["diff_sha256"])

    def test_a_git_call_that_fails_is_a_failure_not_a_clean_tree(self) -> None:
        """A checkout whose HEAD git cannot read has no identity: it raises, never "clean, no commit" (audit B8)."""
        for name, setup in (("plain", ()), ("empty", (("init", "-q"),))):
            repo = self.root / name
            repo.mkdir()
            for args in setup:
                self.git(repo, *args)
            with self.assertRaisesRegex(RuntimeError, "failed"):
                checkout(repo)

    def test_a_timing_is_refused_without_a_commit(self) -> None:
        """A timing sample names the commit of every checkout it ran, or nothing is written (audit B8)."""
        timing = Store("timing/session", self.root)
        for provenance in (None, {"run": "r"}, {"checkouts": {}},
                           {"checkouts": {"tip": {"checkout": "tip", "git_sha": None, "diff_sha256": None}}}):
            with self.assertRaisesRegex(ValueError, "against the commits it ran"):
                timing.put({"cell": "t"}, output={"ms": 1.0}, provenance=provenance)
        self.assertEqual(locations(self.root), [])
        timing.put({"cell": "t"}, output={"ms": 1.0}, provenance=COMMITTED)
        self.assertEqual(locations(self.root), ["timing/session"])

    def test_a_record_carries_no_machine_path_of_any_root(self) -> None:
        """Any absolute path is refused, a home directory's, a container's or a rented box's alike (audit C4); a ratio,
        a relative path, a URL and a path spelled from `~` are not paths on a machine."""
        for sample in ({"output": {"fsla_file": f"{HOME}/wt/fsla/__init__.py"}},
                       {"error": f'File "{HOME}/bench/engine.py", line 1'},
                       {"output": {"ms": 1.0}, "provenance": {"venv": f"{HOME}/venv"}},
                       {"output": {"checkout": f"{BOX}/measure"}},
                       {"error": f"Traceback:\n{BOX}/tools/lab.py raised"},
                       {"output": {f"{BOX}/x": 1}}):
            with self.assertRaisesRegex(ValueError, "machine path"):
                self.store.put({"cell": "p"}, **sample)
        self.assertIsNone(self.store.get(key({"cell": "p"})))
        self.store.put({"cell": "p"}, output={"fsla_file": "fsla/__init__.py", "rate": "cycles/window", "per": "1/2",
                                              "url": "https://example.com/a/b", "home": "~/venv/bin/python"})
        self.assertEqual(check(self.root), [])

    def test_a_stored_record_is_checked_for_consistency_and_a_system_path_is_kept(self) -> None:
        """The path rule is the writer's: a record stored before it, never rewritten, is checked for its consistency alone;
        and a system path is a fact of the environment, kept where a sample is written."""
        sample = self.store.put({"cell": "q"}, output={"python": "/opt/venv/bin/python", "cuda": "/usr/local/cuda"})
        (self.store.dir / sample["output"]).write_text(json.dumps({"fsla_file": f"{BOX}/fsla/__init__.py"}))
        self.assertEqual(check(self.root), [])

    def test_a_directory_of_kept_files_parses(self) -> None:
        lab = self.root / "lab"
        (lab / "add_rate").mkdir(parents=True)
        (lab / "add_rate" / "sm_90a.json").write_text(json.dumps({"rows": [1, 2]}))
        (lab / "add_rate" / "ledger.csv").write_text(f"==PROF== Connected to process 1 ({BOX}/add_rate)\na,b\n1,2\n")
        self.assertEqual(check_files(lab), [])
        (lab / "add_rate" / "broken.json").write_text("{")
        (lab / "add_rate" / "quote.csv").write_text('a,"b\n')
        self.assertEqual(sorted(p.split(":")[0] for p in check_files(lab)), ["add_rate/broken.json", "add_rate/quote.csv"])

    def test_portable_makes_checkout_paths_relative_and_other_home_paths_tilde(self) -> None:
        home, root, other = Path.home(), self.root / "bench", self.root / "fsla"
        root.mkdir()
        other.mkdir()
        text = f'File "{root.resolve()}/pkg/engine.py"\nFile "{other.resolve()}/ops.py"\nFile "{home}/venv/lib/torch.py"'
        self.assertEqual(portable(text, root, other), 'File "pkg/engine.py"\nFile "ops.py"\nFile "~/venv/lib/torch.py"')

    def test_the_default_backend_is_the_machines_store(self) -> None:
        saved = os.environ.get(config.POINTER)
        self.addCleanup(lambda: (os.environ.pop(config.POINTER, None) if saved is None
                                 else os.environ.__setitem__(config.POINTER, saved), config.reload()))
        (self.root / "config").mkdir()
        os.environ[config.POINTER] = str(self.root / "config")
        config.reload()
        with self.assertRaisesRegex(SystemExit, "store.json"):
            backend()
        (self.root / "config" / "store.json").write_text(json.dumps({"root": str(self.root / "data")}))
        config.reload()
        self.assertEqual(backend(), self.root / "data" / "records")
        self.assertEqual(Store("tool").dir, self.root / "data" / "records" / "tool")


if __name__ == "__main__":
    unittest.main()
