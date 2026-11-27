"""The timing session program (`fsla_devtools.timing.session`) on stub entries, no device: a runfiles tree laid out as
`bazel/measure.bzl` lays one out -- the session's manifest, each entry's description and bundle manifest, each cell's
description, and the bundle the entries import from -- and the program run on it.

- A host that declares no clock lock is refused unless the session claims `unlocked`, and the record says which; a
  locked host proves its clock before and after.
- No member set up: the session fails, naming the first refusal, and keeps its file.
- A bound module loaded from outside its entry's bundle, or a worker drawing with other code than its cell's
  description, is that member's failure.
- The order's seed is the run's, stored; two runs walk different orders.
- The session holds the GPU and the host budget whole, and its workers run at its own priority.
- A null gate times each member against a copy of itself in a second worker and finds a worker's bias; a copy that
  could not run is no finding, named. The memory pass takes each member alone, a member that cannot set up its row's
  failure.
- Run as the launcher runs it, it stores ONE sample whose semantics name what ran and whose provenance names each
  bundle's commit, and deletes its scratch; `--no-store` keeps the file and stores nothing.
`python -m unittest tests.test_session`"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fsla_devtools import config
from fsla_devtools.cells import draw_digest
from fsla_devtools.timing import session

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STAMP = {"checkout": "tip", "branch": "main", "git_sha": "a" * 40, "diff_sha256": None}


class Session(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        #: reached through a link, as a run target's runfiles are reached through Bazel's output links
        (self.tmp / "out" / "session.runfiles").mkdir(parents=True)
        (self.tmp / "bin").symlink_to(self.tmp / "out")
        self.runfiles = self.tmp / "bin" / "session.runfiles"
        bundle = self.runfiles / "_main"
        (bundle / "measure").mkdir(parents=True)
        #: the bundle: the stub entries, their provider and the package a worker imports, by link as runfiles are
        for name in ("fake_entries.py", "fake_provider.py"):
            (bundle / name).symlink_to(HERE / name)
        (bundle / "fsla_devtools").symlink_to(ROOT / "fsla_devtools")
        cells = self.runfiles / "cells"
        cells.mkdir()
        for tokens in (8, 16):
            (cells / f"t{tokens}.json").write_text(json.dumps(
                {"name": f"t{tokens}", "data": "fake_provider:tokens", "params": {"tokens": tokens}, "draw": draw_digest()}))
        (bundle / "measure" / "tip.bundle.json").write_text(json.dumps(
            {"stamp": STAMP, "digest": "d" * 64, "files": 3, "environment": "env0"}))
        (self.tmp / "config").mkdir()
        self.config({"gpu_lock": str(self.tmp / "locks" / "gpu.lock"), "lock_dir": str(self.tmp / "locks"),
                     "budget_slots": 2, "scratch": str(self.tmp / "scratch")})
        self.saved = {k: os.environ.get(k) for k in (config.POINTER, "FAKE_CLOCK_GHZ")}
        os.environ[config.POINTER] = str(self.tmp / "config")
        config.reload()

    def tearDown(self):
        for k, v in self.saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        config.reload()

    def config(self, host: dict, clock: dict | None = None) -> None:
        (self.tmp / "config" / "host.json").write_text(json.dumps(host))
        if clock is not None:
            (self.tmp / "config" / "clock.json").write_text(json.dumps(clock))
        config.reload()

    def entry(self, name: str, executor: str, cells=("t8",), params=None, binds=("fake_entries",), kind="timed") -> str:
        path = f"_main/measure/{name}.entry.json"
        (self.runfiles / path).write_text(json.dumps({
            "owner": f"//measure:{name}", "kind": kind, "executor": f"fake_entries:{executor}", "params": params or {},
            "binds": list(binds), "cells": [f"cells/{c}.json" for c in cells],
            "bundle": {"root": "_main", "manifest": "_main/measure/tip.bundle.json", "python": sys.executable}}))
        return path

    def manifest(self, entries, claim="unlocked", clock=None) -> dict:
        return {"session": "//measure:session", "claim": claim, "rounds": 2, "reps": 3, "warmup": 10,
                "label": "@fsla//measure:session", "entries": entries, "clock": clock}

    def measure(self, manifest: dict, run: str = "run-1"):
        return session.measure(manifest, self.runfiles, run=run, scratch=self.tmp / "scratch" / run,
                               read_devices=lambda: [{"name": "stub", "driver": "0", "compute_cap": "0.0"}])

    def test_an_unlocked_host_is_refused_unless_the_session_claims_unlocked_and_the_record_says_which(self):
        fast = self.entry("fast", "fixed", params={"ms": 0.5})
        with self.assertRaisesRegex(session.Refused, "declares no clock lock"):
            self.measure(self.manifest([fast], claim=""))
        doc, semantics, _provenance = self.measure(self.manifest([fast]))
        self.assertEqual((doc["clock"]["locked"], doc["clock"]["declared"], semantics["clock"]), (False, None, "unlocked"))
        self.assertEqual(len(doc["samples"]), 2 * 3)

    def test_a_locked_host_proves_its_clock_before_and_after(self):
        self.config(json.loads((self.tmp / "config" / "host.json").read_text()),
                    {"ghz": 1.5, "lock": ["true"], "unlock": ["true"]})
        fast = self.entry("fast", "fixed", params={"ms": 0.5})
        clock = self.entry("clock", "clock", cells=(), binds=(), kind="clock")
        with self.assertRaisesRegex(session.Refused, "names no clock reader"):
            self.measure(self.manifest([fast], claim=""))
        os.environ["FAKE_CLOCK_GHZ"] = "1.2"
        with self.assertRaisesRegex(session.Refused, "CLOCK: .* before the session"):
            self.measure(self.manifest([fast], claim="", clock=clock))
        os.environ["FAKE_CLOCK_GHZ"] = "1.5"
        doc, semantics, _provenance = self.measure(self.manifest([fast], claim="", clock=clock))
        self.assertEqual((doc["clock"]["locked"], doc["clock"]["before"], doc["clock"]["after"], semantics["clock"]),
                         (True, 1.5, 1.5, "locked"))

    def test_a_session_in_which_nothing_set_up_fails_naming_the_first_refusal_and_keeps_its_file(self):
        unbuilt = self.entry("unbuilt", "unbuilt", cells=("t8", "t16"))
        with self.assertRaisesRegex(session.Refused, r"no member .* set up(.|\n)*//measure:unbuilt on t8(.|\n)*no kernel"):
            self.measure(self.manifest([unbuilt]))
        kept = json.loads((self.tmp / "scratch" / "run-1" / "session.json").read_text())
        self.assertEqual([m["status"] for m in kept["members"]], ["failed", "failed"])

    def test_a_bound_module_from_outside_the_bundle_and_a_foreign_draw_are_the_members_failures(self):
        fast = self.entry("fast", "fixed", params={"ms": 0.5})
        outside = self.entry("outside", "fixed", cells=("t16",), params={"ms": 0.5}, binds=("fake_entries", "json"))
        doc, _semantics, _provenance = self.measure(self.manifest([fast, outside]))
        members = {m["owner"]: m for m in doc["members"]}
        self.assertEqual(members["//measure:fast"]["status"], "ok")
        self.assertEqual(members["//measure:fast"]["loaded"], {"fake_entries": "fake_entries.py"})
        self.assertEqual(members["//measure:outside"]["status"], "failed")
        self.assertIn("json from", members["//measure:outside"]["error"])
        (self.runfiles / "cells" / "t16.json").write_text(json.dumps(
            {"name": "t16", "data": "fake_provider:tokens", "params": {"tokens": 16}, "draw": "other"}))
        foreign = self.entry("foreign", "fixed", cells=("t16",), params={"ms": 0.5})
        doc, _semantics, _provenance = self.measure(self.manifest([fast, foreign]))
        self.assertIn("draws cells with other code", {m["owner"]: m for m in doc["members"]}["//measure:foreign"]["error"])

    def test_the_order_is_seeded_from_the_run_and_two_runs_walk_different_orders(self):
        entries = [self.entry(f"e{i}", "fixed", params={"ms": float(i)}) for i in range(4)]
        orders = []
        for run in ("run-a", "run-b"):
            doc, _semantics, _provenance = self.measure(self.manifest(entries), run=run)
            self.assertEqual(doc["seed"], session.seed_of(run))
            orders.append([s["member"] for s in doc["samples"]])
        self.assertNotEqual(*orders)

    def test_the_session_holds_the_gpu_and_the_whole_host_budget_and_its_workers_run_at_its_priority(self):
        probe = self.entry("probe", "probe")
        doc, _semantics, _provenance = self.measure(self.manifest([probe]))
        built = doc["members"][0]["built"]
        self.assertTrue(built["gpu"] and built["host"])
        self.assertEqual(built["nice"], os.nice(0))
        self.assertEqual(len(list((self.tmp / "locks").glob("fsla_host_budget_slot_*.lock"))), 2)

    def test_two_stopwatches_fail_the_session(self):
        with self.assertRaisesRegex(session.Refused, "one stopwatch"):
            self.measure(self.manifest([self.entry("fast", "fixed", params={"ms": 0.5}),
                                        self.entry("other", "other_clock", cells=("t16",))]))

    def test_a_null_gate_times_each_member_against_its_copy_in_a_second_worker_and_finds_a_biased_one(self):
        os.environ["FAKE_BIAS_DIR"] = str(self.tmp)
        self.addCleanup(os.environ.pop, "FAKE_BIAS_DIR", None)
        fair = self.entry("fair", "fixed", params={"ms": 0.125})
        biased = self.entry("biased", "biased")
        doc, semantics, _provenance = self.measure({**self.manifest([fair, biased]), "mode": "null"})
        rows = {r["owner"]: r for r in doc["null"]}
        self.assertEqual((rows["//measure:fair"]["trusted"], rows["//measure:fair"]["ratio_median"]), (True, 1.0))
        self.assertEqual((rows["//measure:biased"]["trusted"], rows["//measure:biased"]["ratio_median"]), (False, 0.5))
        #: each copy in a worker of its own
        pids = {m["owner"]: m["built"]["pid"] for m in doc["members"] if "pid" in m["built"]}
        self.assertNotEqual(pids["//measure:biased#a"], pids["//measure:biased#b"])
        self.assertTrue(semantics["null_gate"])

    def test_a_null_gate_whose_copy_could_not_run_has_no_finding_and_says_why(self):
        doc, _semantics, _provenance = self.measure({**self.manifest([self.entry("fast", "fixed", params={"ms": 1.0}),
                                                                      self.entry("unbuilt", "unbuilt")]),
                                                     "mode": "null"})
        row = {r["owner"]: r for r in doc["null"]}["//measure:unbuilt"]
        self.assertIsNone(row["trusted"])
        self.assertIn("no kernel", row["error"])

    def test_the_memory_pass_takes_each_member_alone_and_records_one_that_cannot_set_up(self):
        manifest = {"session": "//measure:memory", "mode": "memory", "calls": 5, "label": "@ws//measure:memory",
                    "entries": [self.entry("fast", "fixed", params={"ms": 1.0}), self.entry("unbuilt", "unbuilt")]}
        out, semantics, provenance = self.measure(manifest)
        self.assertEqual([(r["owner"], r["status"]) for r in out["rows"]],
                         [("//measure:fast", "ok"), ("//measure:unbuilt", "failed")])
        self.assertEqual((out["rows"][0]["calls"], semantics["calls"], semantics["mode"]), (5, 5, "memory"))
        self.assertEqual(provenance["checkouts"]["//measure:fast"], STAMP)
        with self.assertRaisesRegex(session.Refused, "no member"):
            self.measure({**manifest, "entries": [manifest["entries"][1]]})

    def run_program(self, *args: str) -> subprocess.CompletedProcess:
        path = "_main/measure/session.session.json"
        (self.runfiles / path).write_text(json.dumps(self.manifest([self.entry("fast", "fixed", params={"ms": 0.5})])))
        return subprocess.run([sys.executable, "-m", "fsla_devtools.timing.session", "--runfiles", str(self.runfiles),
                               "--manifest", path, *args], capture_output=True, text=True, timeout=300,
                              env={**os.environ, "PYTHONPATH": os.pathsep.join(
                                  p for p in (str(ROOT), os.environ.get("PYTHONPATH")) if p)})

    def test_the_program_stores_one_sample_naming_what_ran_and_each_bundles_commit(self):
        from fsla_devtools.results import Store

        records = self.tmp / "records"
        done = self.run_program("--store-root", str(records))
        self.assertEqual(done.returncode, 0, done.stderr)
        (sample,) = Store("@fsla//measure:session", records).samples()
        semantics = sample["semantics"]
        self.assertEqual([e["owner"] for e in semantics["entries"]], ["//measure:fast"])
        self.assertEqual(semantics["entries"][0]["bundle"], {"digest": "d" * 64, "environment": "env0"})
        self.assertEqual(semantics["entries"][0]["cells"][0]["name"], "t8")
        self.assertEqual((semantics["clock"], semantics["rounds"], semantics["reps"]), ("unlocked", 2, 3))
        self.assertEqual(sample["provenance"]["checkouts"], {"//measure:fast": STAMP})
        self.assertEqual(sample["summary"]["samples"], 2 * 3)
        stored = json.loads((Store("@fsla//measure:session", records).output(sample)).read_text())
        self.assertEqual(stored["seed"], session.seed_of(sample["run"]))
        self.assertEqual(list((self.tmp / "scratch" / "timing").iterdir()), [])

    def test_no_store_keeps_the_session_and_stores_nothing(self):
        done = self.run_program("--no-store", "--store-root", str(self.tmp / "records"))
        self.assertEqual(done.returncode, 0, done.stderr)
        (kept,) = (self.tmp / "scratch" / "timing").glob("*/session.json")
        self.assertEqual(len(json.loads(kept.read_text())["samples"]), 2 * 3)
        self.assertFalse((self.tmp / "records").exists())


if __name__ == "__main__":
    unittest.main()
