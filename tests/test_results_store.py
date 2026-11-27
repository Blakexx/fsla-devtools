"""The store's contract, on a temporary backend (no GPU): a sample's folder its producing target's label, one file a
sample named by its run and written once (no lock, no rewrite), its semantics and their key in it, an output file
moved in with the parts its execution wrote, a checkout's identity or a failure to read it, a timing refused without a
commit, no machine path in anything written -- a path in someone's home directory, refused at the door, while a system
path is kept and a stored sample is checked for its consistency alone -- and kept bytes re-filed byte-identical, their
own date and provenance, their machine paths stated. `python -m unittest tests.test_results_store`"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from fsla_devtools import config
from fsla_devtools.results import Store, backend, check, checkout, digest, key, label_path, labels, outputs, portable
from fsla_devtools.results.refile import refile

#: a home-directory prefix, joined so this file carries none
HOME = "/".join(("", "home", "someone"))
#: a rented box's container checkout: no home directory, still a path on one machine (audit C4)
BOX = "/".join(("", "root", "work", "fsla"))
COMMITTED = {"checkouts": {"tip": {"checkout": "tip", "git_sha": "a" * 40, "diff_sha256": None}}}
LABEL = "@fsla//tool/sub:store_it"


class StoreContract(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(LABEL, self.root)
        self.runs = iter(f"20261127T0000{i:02d}Z-test{i}" for i in range(60))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def put(self, semantics, store=None, **kwargs):
        return (store or self.store).put(semantics, run=next(self.runs), **kwargs)

    def test_a_label_is_its_folder_and_every_sample_is_its_own_file_newest_first(self) -> None:
        self.assertEqual(label_path("@fsla//measure/lab:add_rate.run"), Path("fsla/measure/lab/add_rate.run"))
        self.assertEqual(label_path("@fsla//:container_check"), Path("fsla/container_check"))
        for bad in ("fsla/x", "//measure:x", "@fsla//a/../b:x", "@Fsla//a:x", "@fsla//a:b/c"):
            with self.assertRaises(ValueError):
                Store(bad, self.root)
        sem = {"cell": "a", "binary": "b1"}
        first = self.put(sem, output={"ms": 1.0}, provenance={"git_sha": "x"}, wall_s=0.5)
        self.put(sem, error="boom")
        self.put({"cell": "b"}, output={"ms": 3.0})
        last = self.put(sem, output={"ms": 2.0})
        self.assertEqual(self.store.dir, self.root / "fsla/tool/sub/store_it")
        self.assertEqual(sorted(p.name for p in self.store.dir.glob("*.json")), [f"{r}.json" for r in
                         sorted(s["run"] for s in self.store.samples())])
        self.assertEqual([s["ok"] for s in self.store.samples(key(sem))], [True, False, True])
        self.assertEqual(self.store.samples(key(sem))[0]["run"], last["run"])
        self.assertEqual((first["key"], first["semantics"], first["label"]), (key(sem), sem, LABEL))
        self.assertIn("host", first["provenance"])
        self.assertEqual(json.loads(self.store.output(self.store.samples(key(sem))[0]).read_text()), {"ms": 2.0})
        self.assertEqual(labels(self.root), [LABEL])
        self.assertEqual(check(self.root), [])
        self.assertEqual(list(self.root.rglob("*.lock")), [])

    def test_a_sample_is_written_once_and_never_again(self) -> None:
        self.store.put({"cell": "a"}, output=1, run="20261127T000000Z-once")
        with self.assertRaises(FileExistsError):
            self.store.put({"cell": "a"}, output=2, run="20261127T000000Z-once")
        with self.assertRaisesRegex(ValueError, "is not"):
            self.store.put({"cell": "a"}, output=2, run="tuesday")
        self.assertEqual(json.loads(self.store.output(self.store.samples()[0]).read_text()), 1)

    def test_unless_stored_asks_the_label_whether_it_holds_the_outcome(self) -> None:
        """A build output stored again stores nothing; a changed output, or a failure not yet held, is a new sample."""
        sem = {"source": "//:manifest", "inputs": {"//:library": "a"}}
        src = self.root / "m.json"
        src.write_text('{"a": 1}\n')
        self.assertIsNotNone(self.put(sem, output_file=src, unless_stored=True))
        src.write_text('{"a": 1}\n')
        self.assertIsNone(self.put(sem, output_file=src, unless_stored=True))
        self.assertTrue(src.exists())
        src.write_text('{"a": 2}\n')
        self.assertIsNotNone(self.put(sem, output_file=src, unless_stored=True))
        self.assertIsNotNone(self.put(sem, error="refused: no device", unless_stored=True))
        self.assertIsNone(self.put(sem, error="refused: no device", unless_stored=True))
        self.put(sem, output={"a": 3})
        self.assertIsNone(self.put(sem, output={"a": 3}, unless_stored=True))
        self.assertEqual(len(self.store.samples(key(sem))), 4)

    def test_an_output_file_is_moved_in_with_its_suffix_and_a_failure_has_none(self) -> None:
        src = self.root / "capture.csv"
        src.write_text("a,b\n")
        sample = self.put({"cell": "c"}, output_file=src)
        self.assertEqual(sample["output"], f"{sample['run']}/output.csv")
        self.assertFalse(src.exists())
        failed = self.put({"cell": "c"}, error="boom")
        self.assertNotIn("output", failed)
        self.assertFalse((self.store.dir / failed["run"]).exists())

    def test_a_sample_is_exactly_one_of_output_or_error(self) -> None:
        with self.assertRaises(ValueError):
            self.put({"x": 1})
        with self.assertRaises(ValueError):
            self.put({"x": 1}, output=1, error="e")

    def test_outputs_walks_a_prefix_and_skips_failures(self) -> None:
        self.put({"cell": "x"}, Store("@b//mqar:a", self.root), output={"acc": 1})
        self.put({"cell": "y"}, Store("@b//mqar:a", self.root), error="oom")
        self.put({"cell": "z"}, Store("@b//mqar:b", self.root), output={"acc": 2})
        self.put({"cell": "w"}, Store("@b//mqarx:a", self.root), output={"acc": 3})
        self.assertEqual(sorted(o["acc"] for _s, o in outputs("@b//mqar:", self.root)), [1, 2])
        self.assertEqual([o["acc"] for _s, o in outputs("@b//mqar:b", self.root)], [2])

    def test_check_finds_an_edited_sample_a_lock_a_stray_and_a_target_under_a_target(self) -> None:
        sample = self.put({"cell": "d"}, output=1)
        self.assertEqual(check(self.root), [])
        path = self.store.dir / f"{sample['run']}.json"
        edited = json.loads(path.read_text())
        edited["semantics"]["cell"] = "e"
        path.write_text(json.dumps(edited))
        (self.store.dir / "x.lock").write_text("")
        (self.root / "fsla" / "stray.json").write_text("{}")
        self.put({"cell": "f"}, Store(LABEL.replace(":store_it", "/store_it:inner"), self.root), output=1)
        found = "\n".join(check(self.root))
        for what in ("does not recompute", "a lock or a write in progress", "stray.json: neither a sample",
                     "a folder in a target's folder that is no sample's"):
            self.assertIn(what, found)

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
        timing = Store("@fsla//measure:session", self.root)
        for provenance in (None, {"run": "r"}, {"checkouts": {}},
                           {"checkouts": {"tip": {"checkout": "tip", "git_sha": None, "diff_sha256": None}}}):
            with self.assertRaisesRegex(ValueError, "against the commits it ran"):
                self.put({"cell": "t"}, timing, output={"ms": 1.0}, provenance=provenance, timing=True)
        self.assertEqual(labels(self.root), [])
        self.put({"cell": "t"}, timing, output={"ms": 1.0}, provenance=COMMITTED, timing=True)
        self.assertEqual(labels(self.root), ["@fsla//measure:session"])

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
                self.put({"cell": "p"}, **sample)
        self.assertEqual(self.store.samples(), [])
        self.put({"cell": "p"}, output={"fsla_file": "fsla/__init__.py", "rate": "cycles/window", "per": "1/2",
                                        "url": "https://example.com/a/b", "home": "~/venv/bin/python"})
        self.assertEqual(check(self.root), [])

    def test_a_stored_sample_is_checked_for_consistency_and_a_system_path_is_kept(self) -> None:
        """The path rule is the writer's: a sample stored before it, never rewritten, is checked for its consistency alone;
        and a system path is a fact of the environment, kept where a sample is written."""
        sample = self.put({"cell": "q"}, output={"python": "/opt/venv/bin/python", "cuda": "/usr/local/cuda"})
        self.store.output(sample).write_text(json.dumps({"fsla_file": f"{BOX}/fsla/__init__.py"}))
        self.assertEqual(check(self.root), [])

    def test_parts_are_moved_in_beside_the_output_and_checked(self) -> None:
        """The files one execution wrote beside its output are its sample's parts, each under its name; a missing part is
        found by the check, a part named outside its folder or carrying a machine path is refused, and a failure has
        none."""
        out, row = self.root / "result.json", self.root / "row0.json"
        out.write_text('{"rows": 1}\n')
        row.write_text('{"stamps": []}\n')
        sample = self.put({"cell": "t"}, output_file=out, parts={"trace/row0.json": row})
        self.assertEqual(sample["parts"], ["trace/row0.json"])
        self.assertEqual((self.store.parts(sample) / "trace/row0.json").read_text(), '{"stamps": []}\n')
        self.assertFalse(row.exists())
        self.assertEqual(check(self.root), [])
        (self.store.parts(sample) / "trace/row0.json").unlink()
        self.assertTrue(any("part trace/row0.json is missing" in p for p in check(self.root)))
        for name in ("../row.json", "/abs.json", "a b.json"):
            row.write_text("{}\n")
            with self.assertRaisesRegex(ValueError, "plain names"):
                self.put({"cell": "u"}, output={"a": 1}, parts={name: row})
        row.write_text(json.dumps({"binary": f"{HOME}/lab/x"}))
        with self.assertRaisesRegex(ValueError, "machine path"):
            self.put({"cell": "u"}, output={"a": 1}, parts={"trace/row0.json": row})
        with self.assertRaisesRegex(ValueError, "no parts"):
            self.put({"cell": "u"}, error="boom", parts={"trace/row0.json": row})

    def kept(self) -> Path:
        """A data repository's directory of files kept as written: a result, its trace and a dump naming a box's path."""
        lab = self.root / "checkout" / "lab" / "add_rate"
        (lab / "sm_90a.trace").mkdir(parents=True)
        (lab / "sm_90a.json").write_text('{"date": "2026-11-15T22:32:32+00:00",  "rows": [1, 2]}\n')
        (lab / "sm_90a.trace" / "row0.json").write_text('{"stamps":[]}\n')
        (lab / "ledger.csv").write_text(f"==PROF== Connected to process 1 ({BOX}/add_rate)\na,b\n1,2\n")
        return self.root / "checkout"

    def test_kept_bytes_are_refiled_as_a_sample_of_their_own_bytes_date_and_provenance(self) -> None:
        """Their bytes copied in unchanged, their parts beside them, their date the sample's, no host added, marked with
        where they were kept and the machine paths they carry; the same run refiled again writes nothing."""
        checkout, records = self.kept(), self.root / "records"
        lab = Store("@fsla//measure/lab:add_rate.run", records)
        sem = {"experiment": "add_rate", "arch": "sm_90a"}
        mark = {"backfilled": {"file": "lab/add_rate/sm_90a.json", "commit": "c" * 40}}
        args = {"at": "2026-11-15T22:32:32Z", "provenance": {"device": "H100"}}
        sample = lab.refile(sem, run="20261115T223232Z-backfill", mark=mark,
                            output_file=checkout / "lab/add_rate/sm_90a.json",
                            parts={"trace/row0.json": checkout / "lab/add_rate/sm_90a.trace/row0.json"}, **args)
        self.assertEqual((sample["utc"], sample["provenance"]), ("2026-11-15T22:32:32Z", {"device": "H100"}))
        self.assertEqual(sample["backfilled"], {"file": "lab/add_rate/sm_90a.json", "commit": "c" * 40, "machine_paths": []})
        self.assertEqual(digest(lab.output(sample)), digest(checkout / "lab/add_rate/sm_90a.json"))
        self.assertTrue((checkout / "lab/add_rate/sm_90a.json").exists())
        self.assertIsNone(lab.refile(sem, run="20261115T223232Z-backfill", mark=mark,
                                     output_file=checkout / "lab/add_rate/sm_90a.json", **args))
        dump = lab.refile({**sem, "kind": "ledger"}, run="20261116T000000Z-backfill", output_file=checkout /
                          "lab/add_rate/ledger.csv", mark={"migrated": {"from": "x/1.json", "commit": "c" * 40}}, **args)
        self.assertEqual(dump["migrated"]["machine_paths"], [f"{BOX}/add_rate"])
        self.assertEqual(check(records), [])
        stated = lab.refile(sem, run="20261117T000000Z-backfill", output_file=checkout / "lab/add_rate/sm_90a.json",
                            mark=mark, at=args["at"], provenance={"venv": f"{HOME}/venv"})
        self.assertEqual(stated["backfilled"]["machine_paths"], [f"{HOME}/venv"])
        with self.assertRaisesRegex(ValueError, "never a machine path"):
            lab.refile(sem, run="20261118T000000Z-backfill", output_file=checkout / "lab/add_rate/ledger.csv",
                       mark={"backfilled": {"file": f"{HOME}/x", "commit": "c"}}, **args)

    def test_the_refiling_writes_a_plan_and_tables_every_file(self) -> None:
        checkout, records = self.kept(), self.root / "records"
        mark = {"backfilled": {"file": "lab/add_rate", "commit": "c" * 40}}
        plan = {"samples": [
            {"output": "lab/add_rate/ledger.csv", "label": "@fsla//tools:lab", "semantics": {"e": "add_rate"},
             "run": "20261116T000000Z-a", "at": "2026-11-16T00:00:00Z", "provenance": {}, "mark": mark},
            {"output": "lab/add_rate/sm_90a.json", "label": "@fsla//tools:lab", "semantics": {"e": "add_rate"},
             "run": "20261115T223232Z-a", "at": "2026-11-15T22:32:32Z", "provenance": {}, "mark": mark,
             "parts": {"trace/row0.json": "lab/add_rate/sm_90a.trace/row0.json"}}]}
        rows = refile(plan, checkout, records)
        self.assertEqual([(r["file"], r["run"], r["identical"]) for r in rows],
                         [("lab/add_rate/sm_90a.json", "20261115T223232Z-a", "yes"),
                          ("lab/add_rate/sm_90a.trace/row0.json", "20261115T223232Z-a", "yes"),
                          ("lab/add_rate/ledger.csv", "20261116T000000Z-a", "yes")])
        self.assertEqual(rows, refile(plan, checkout, records))
        self.assertEqual([s["run"] for s in Store("@fsla//tools:lab", records).samples(key({"e": "add_rate"}))],
                         ["20261116T000000Z-a", "20261115T223232Z-a"])

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
        self.assertEqual(Store(LABEL).dir, self.root / "data" / "records" / "fsla/tool/sub/store_it")


if __name__ == "__main__":
    unittest.main()
