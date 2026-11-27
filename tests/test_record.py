"""An instrument run and a build output's record (`fsla_devtools.instrument`, `fsla_devtools.record`), on temporary
trees and a temporary store (no GPU): a tool runs in a tree of copies, never the files it was linked from, sees no device
and no machine's home unless it is a device instrument, and its refusal is its output while a crash or a missing JSON
fails; a record stores each output once -- the same outcome again stores nothing -- a refusal as a failed sample naming
why, and a device instrument runs before its output is stored. `python -m unittest tests.test_record`"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from fsla_devtools import instrument, record
from fsla_devtools.results import Store, key

STAMP = {"git_sha": "a" * 40, "branch": "main", "checkout": "tip", "diff_sha256": None}
#: the store target's own label, which keys its samples
LABEL = "@fsla//measure:store_manifest"
#: a tool that reports where it ran and what it saw, or refuses or fails as its first argument says
TOOL = """\
import json, os, sys
from pathlib import Path
out = Path(sys.argv[sys.argv.index("--json") + 1])
how = sys.argv[1]
if how == "crash":
    sys.exit(3)
if how == "silent":
    sys.exit(0)
if how == "refuse":
    out.write_text(json.dumps({"refused": "no such kernel"}))
    sys.exit(0)
if how == "count":
    out.write_text(json.dumps({"data": Path("data.txt").read_text()}))
    sys.exit(0)
out.write_text(json.dumps({"here": str(Path(__file__).resolve().parent), "data": Path("data.txt").read_text(),
                           "devices": os.environ.get("CUDA_VISIBLE_DEVICES"), "home": os.environ.get("HOME"),
                           "marker": os.environ.get("RECORD_TEST_MARKER")}))
"""


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.source = self.dir / "checkout"
        self.source.mkdir()
        (self.source / "tool.py").write_text(TOOL)
        (self.source / "data.txt").write_text("read")
        #: the files reach the instrument as links into the checkout, as an action's inputs and a run's runfiles do
        self.links = self.dir / "links"
        self.links.mkdir()
        for name in ("tool.py", "data.txt"):
            (self.links / name).symlink_to(self.source / name)
        self.files = [("tool.py", str(self.links / "tool.py")), ("data.txt", str(self.links / "data.txt"))]

    def tearDown(self) -> None:
        self.tmp.cleanup()


class InstrumentRun(Base):
    def run_tool(self, how: str, device: bool = False) -> dict:
        return instrument.run(self.files, "tool.py", [how], self.dir / "out.json", python=sys.executable, device=device)

    def test_the_tool_runs_in_a_tree_of_copies_and_sees_no_device_or_home(self) -> None:
        out = self.run_tool("report")
        self.assertNotEqual(Path(out["here"]), self.source.resolve())
        self.assertEqual(out["data"], "read")
        self.assertEqual(out["devices"], "")
        self.assertNotEqual(out["home"], os.environ.get("HOME"))
        self.assertIsNone(out["marker"])

    def test_a_device_instrument_runs_in_the_runs_environment(self) -> None:
        os.environ["RECORD_TEST_MARKER"] = "seen"
        try:
            out = self.run_tool("report", device=True)
        finally:
            del os.environ["RECORD_TEST_MARKER"]
        self.assertEqual(out["marker"], "seen")

    def test_a_refusal_is_the_output_and_a_crash_or_a_missing_json_fails(self) -> None:
        self.assertEqual(self.run_tool("refuse"), {"refused": "no such kernel"})
        for how in ("crash", "silent"):
            with self.assertRaises(SystemExit):
                self.run_tool(how)


class Record(Base):
    def setUp(self) -> None:
        super().setUp()
        self.runfiles = self.dir / "runfiles"
        (self.runfiles / "_main" / "out").mkdir(parents=True)
        self.store_root = self.dir / "records"

    def described(self, outputs: list[dict], instrument_run: dict | None = None) -> str:
        blob = {"source": "//:manifest", "label": LABEL, "inputs": {"//:library": "d" * 64},
                "outputs": outputs, "instrument": instrument_run, "stamp": STAMP}
        (self.runfiles / "_main" / "record.json").write_text(json.dumps(blob))
        return "_main/record.json"

    def main(self, path: str, *extra: str) -> str:
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            self.assertEqual(record.main(["--runfiles", str(self.runfiles), "--record", path,
                                          "--store-root", str(self.store_root), "--scratch", str(self.dir / "scratch"),
                                          *extra]), 0)
        return printed.getvalue()

    def samples(self) -> list[dict]:
        return Store(LABEL, self.store_root).samples()

    def test_a_build_output_is_stored_once_keyed_by_what_it_depends_on(self) -> None:
        built = self.runfiles / "_main" / "out" / "m.json"
        built.write_text('{"kernels": 3}\n')
        path = self.described([{"name": "out/m.json", "path": "_main/out/m.json"}])
        self.assertIn(f"stored: {LABEL} out/m.json sample ", self.main(path))
        self.assertIn("already stored", self.main(path))
        self.assertTrue(built.exists())
        (sample,) = self.samples()
        self.assertEqual(sample["semantics"],
                         {"source": "//:manifest", "file": "out/m.json", "inputs": {"//:library": "d" * 64}})
        self.assertEqual(sample["key"], key(sample["semantics"]))
        self.assertEqual(sample["provenance"]["checkouts"], {"//:manifest": STAMP})
        self.assertEqual(sample["provenance"]["run"], sample["run"])
        built.write_text('{"kernels": 4}\n')
        self.main(path)
        self.assertEqual(len(self.samples()), 2)

    def test_an_instruments_refusal_is_a_failed_sample_naming_why(self) -> None:
        (self.runfiles / "_main" / "out" / "i.json").write_text(json.dumps({"refused": "no such kernel"}))
        path = self.described([{"name": "out/i.json", "path": "_main/out/i.json"}],
                              {"tool": "tool.py", "args": [], "device": False, "files": []})
        self.main(path)
        self.assertIn("already stored", self.main(path))
        (sample,) = self.samples()
        self.assertFalse(sample["ok"])
        self.assertEqual(sample["error"], "refused: no such kernel")

    def test_a_device_instrument_runs_then_is_stored_and_its_scratch_removed(self) -> None:
        for name in ("tool.py", "data.txt"):
            (self.runfiles / "_main" / name).symlink_to(self.source / name)
        files = [["tool.py", "_main/tool.py"], ["data.txt", "_main/data.txt"]]
        path = self.described([{"name": "out/d.json", "path": None}],
                              {"tool": "tool.py", "args": ["count"], "device": True, "files": files})
        self.assertIn("not stored: out/d.json", self.main(path, "--no-store"))
        self.assertTrue((self.dir / "scratch" / "out" / "d.json").is_file())
        self.assertIn(f"stored: {LABEL} out/d.json sample ", self.main(path))
        self.assertFalse((self.dir / "scratch").exists())
        (sample,) = self.samples()
        self.assertTrue(sample["ok"])


if __name__ == "__main__":
    unittest.main()
