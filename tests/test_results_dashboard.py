"""The dashboard reads one run's timing sessions and memory rows from the index: every member's median, its paired ratio
to the reference label named when reading, a member that could not run or failed partway shown by its failure and never
by a median, and the peak its entry reached alone. `python -m unittest tests.test_results_dashboard`"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from test_results_index import isolate_cache
from test_verdict import plant

from fsla_devtools.results import Store
from fsla_devtools.results.dashboard import render


class Dashboard(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        isolate_cache(self, Path(self.tmp.name) / "cache")
        self.root = Path(self.tmp.name) / "records"

    def test_a_session_and_its_memory_rows_are_one_table(self):
        plant(self.root, tip_ms=1.5)
        rows = [{"owner": "tip/carry_forward", "cell": "dense", "status": "ok", "calls": 5,
                 "peak_allocated_bytes": 17_600_000, "peak_reserved_bytes": 52_400_000, "allocated_after_bytes": 1,
                 "allocated_before_build_bytes": 1, "outside_allocator_bytes": 2_100_000, "built": {"device": "gpu"}}]
        Store("timing/memory", self.root).put({"executor": "memory"}, output={"run": "r", "rows": rows},
                                              provenance={"run": "r", "checkouts": {"tip": {"git_sha": "a"}}}, run="r")
        page = render(self.root, reference="master")
        self.assertIn("<h2>session/G/carry_forward+flash</h2>", page)
        self.assertIn(">1.500<", page)
        self.assertIn("failed", page)
        self.assertIn("19.7", page)
        self.assertIn("54.5", page)
        self.assertNotIn(">1.500<", render(self.root))

    def test_a_member_that_failed_partway_shows_its_failure_and_no_median(self):
        """A member that kept samples from before it failed is not a measurement: its row is the failure (audit B10)."""
        plant(self.root, tip_ms=1.5)
        record = json.loads(next((self.root / "timing" / "session").glob("*[0-9a-f].json")).read_text())
        output = self.root / "timing" / "session" / record["samples"][0]["output"]
        session = json.loads(output.read_text())
        tip = next(m for m in session["members"] if m["id"] == "m0")
        tip.update(status="failed", error="the call raised in round 5")
        output.write_text(json.dumps(session))
        page = render(self.root, reference="master")
        self.assertNotIn(">1.500<", page)
        self.assertIn("the call raised in round 5", page)


if __name__ == "__main__":
    unittest.main()
