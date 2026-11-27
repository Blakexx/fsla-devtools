"""THE DIFF: the three comparison rules on planted numbers, and the verdict program they hang on -- two sides, each
produced on its own bundle's interpreter over the same central cells, the claim (`expect`) it keeps or breaks, a cell
neither side produced counting as a comparison that did NOT happen, the raw tensors never leaving the run, and the gate
over a verdict (a failure, or a recorded outcome). `python -m unittest tests.test_diff`"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import torch

from fsla_devtools.cells import descriptions
from fsla_devtools.diff import verdict
from fsla_devtools.diff.strategies import bit_identical, per_slot, support_equal

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

#: two central cells the diffs run over
ANTI, ONEHOT = "carry-anti-64x64-T1024-carried", "carry-onehot-16x16-T1024"


class Rules(unittest.TestCase):
    """The rules alone, on planted numbers: no build, no cells."""

    def test_bit_identity_admits_no_tolerance_and_names_the_worst_slot(self):
        a = torch.tensor([1.0, 2.0, 3.0])
        self.assertTrue(bit_identical(a, a.clone())["same"])
        moved = a.clone()
        moved[2] += 1e-3
        out = bit_identical(a, moved)
        self.assertEqual((out["same"], out["at"], out["failed"]), (False, [2], 1))
        self.assertFalse(bit_identical(a, a.double())["same"])  #: a dtype change is a difference

    def test_the_per_slot_rule_is_the_smallest_clause_and_the_message_names_what_bound_it(self):
        reference = torch.tensor([1e-4, 1.0, 100.0], dtype=torch.float64)
        clauses = [[1e-2, 1e-6]]
        #: inside every clause: 1% of the slot, or the floor below it
        self.assertTrue(per_slot(reference * 1.005, reference, clauses=clauses)["same"])
        out = per_slot(reference + torch.tensor([0.0, 0.0, 2.0]), reference, clauses=clauses)
        self.assertEqual((out["same"], out["at"]), (False, [2]))
        self.assertEqual(out["bound_by"], "clause (0.01, 1e-06)")
        #: two clauses, and the ABSOLUTE one binds the big slot
        out = per_slot(reference + torch.tensor([0.0, 0.0, 0.5]), reference, clauses=[[1e-2, 1e-6], [0.0, 1e-1]])
        self.assertEqual((out["same"], out["bound_by"]), (False, "clause (0.0, 0.1)"))

    def test_a_multilinear_output_adds_its_envelope_and_refuses_to_be_graded_without_one(self):
        reference = torch.tensor([1.0], dtype=torch.float64)
        envelope = torch.tensor([0.5], dtype=torch.float64)
        out = per_slot(reference + 9e-3, reference, clauses=[[1e-2, 0.0]], rtol=1e-2, envelope=envelope)
        self.assertEqual((out["same"], out["bound_by"]), (False, "the envelope"))
        with self.assertRaises(ValueError):
            per_slot(reference, reference, clauses=[[1e-2, 0.0]], rtol=1e-2)

    def test_support_equality_is_about_which_slots_survive_and_then_about_their_values(self):
        a = torch.tensor([1.0, 0.0, 3.0], dtype=torch.float64)
        self.assertTrue(support_equal(a, a.clone())["same"])
        moved = torch.tensor([1.0, 2.0, 3.0], dtype=torch.float64)
        self.assertEqual(support_equal(moved, a)["why"], "the support moved")
        #: the support held, so the values on it are graded and nothing off it is
        out = support_equal(torch.tensor([1.0, 0.0, 3.5], dtype=torch.float64), a, rtol=1e-2)
        self.assertEqual((out["same"], out["at"]), (False, [2]))


class Verdict(unittest.TestCase):
    """The verdict program on a runfiles tree laid out as `diff_verdict`'s instrument lays one out: two bundles, each
    holding the stub sides as copies (a bound module loads from inside its bundle), each side's description and its
    bundle's manifest, and the central cells' descriptions."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tree = Path(self._tmp.name) / "tree"
        described = descriptions()
        #: the sides' environment, this one, as an environment repository links it: its mark resolves to the venv
        (self.tree / "env").mkdir(parents=True)
        (self.tree / "env" / "venv").symlink_to(sys.prefix)
        for name in (ANTI, ONEHOT):
            path = self.tree / "cells" / f"{name}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(described[name]))
        for bundle in ("left", "right"):
            root = self.tree / bundle
            root.mkdir()
            (root / "fake_sides.py").write_bytes((HERE / "fake_sides.py").read_bytes())
            #: the drawing code the side's environment carries
            (root / "fsla_devtools").symlink_to(ROOT / "fsla_devtools")
            (root / "bundle.json").write_text(json.dumps({"digest": bundle * 8, "environment": "env0"}))

    def side(self, name: str, bundle: str, executor: str = "fake_sides:flat", params=None, names=(ANTI, ONEHOT),
             binds=()) -> Path:
        path = self.tree / f"{name}.entry.json"
        path.write_text(json.dumps({
            "owner": f"//{bundle}:{name}", "kind": "side", "device": False, "executor": executor, "params": params or {},
            "binds": list(binds), "cells": [f"cells/{c}.json" for c in names],
            "bundle": {"root": bundle, "manifest": f"{bundle}/bundle.json", "venv": "env/venv/.complete"}}))
        return path

    def run_verdict(self, left: Path, right: Path, strategy: str = "bit-identical", params=None, **claim) -> dict:
        claim = {"expect": "same", "minimum": 1, **claim}
        return verdict.verdict(left, right, strategy=strategy, params=params or {}, threads=1, tree=self.tree, **claim)

    def test_two_sides_that_agree_hold_the_claim_and_the_raw_never_leaves_the_run(self):
        out = self.run_verdict(self.side("a", "left"), self.side("b", "right"))
        self.assertTrue(out["held"])
        self.assertEqual(out["compared"], 2)
        self.assertEqual(sorted(out["cells"][ANTI]["quantities"]), ["state", "y"])
        #: THE RAW STAYS IN THE RUN: no tensor file anywhere in the tree, and the verdict carries verdicts, no numbers
        self.assertEqual(list(self.tree.rglob("*.pt")), [])
        self.assertNotIn("file", json.dumps(out["cells"]))
        self.assertEqual(out["sides"]["left"]["bundle"], "left" * 8)

    def test_a_difference_is_a_claim_not_held_and_the_message_names_the_worst_slot(self):
        out = self.run_verdict(self.side("a", "left"), self.side("b", "right", params={"bump": 0.5, "where": 3}))
        self.assertFalse(out["held"])
        self.assertEqual(out["differing"], [ANTI, ONEHOT])
        self.assertIn("cells differ under bit-identical", out["detail"])
        self.assertIn('"at": [3]', out["detail"])

    def test_the_gate_fails_a_claim_not_held_and_a_recorded_one_passes(self):
        out = self.run_verdict(self.side("a", "left"), self.side("b", "right", params={"bump": 0.5}))
        (self.tree / "verdict.json").write_text(json.dumps(out))
        spec = {"verdict": "//:d", "output": "verdict.json", "on_difference": "fail"}
        self.assertEqual(verdict.check(self.tree, spec), 1)
        self.assertEqual(verdict.check(self.tree, {**spec, "on_difference": "record"}), 0)

    def test_expecting_a_difference_is_the_non_vacuity_half_and_agreement_breaks_that_claim(self):
        seen = self.run_verdict(self.side("a", "left"), self.side("b", "right", params={"bump": 1.0}),
                                expect="different")
        self.assertTrue(seen["held"])
        blind = self.run_verdict(self.side("a", "left"), self.side("b", "right"), expect="different")
        self.assertFalse(blind["held"])
        self.assertIn("the comparison cannot see what it was built to see", blind["detail"])

    def test_a_cell_a_side_could_not_produce_is_not_a_comparison_that_passed(self):
        out = self.run_verdict(self.side("a", "left", names=(ANTI,)),
                               self.side("b", "right", executor="fake_sides:refuses", names=(ANTI,)))
        self.assertFalse(out["held"])
        self.assertEqual(out["unusable"], [ANTI])
        self.assertIn("produced no comparison", out["detail"])

    def test_a_side_that_imported_its_library_from_outside_its_bundle_refuses_to_be_a_side(self):
        """THE TRAP THAT MAKES A COMPARISON VACUOUS: a side that imported another tree would compare that tree."""
        with self.assertRaises(SystemExit):
            self.run_verdict(self.side("a", "left", binds=("fsla_devtools",)), self.side("b", "right"))

    def test_one_bundle_twice_with_one_executor_compared_a_tree_with_itself(self):
        """A kernel against its own fp64 reference shares a bundle by design; the same executor twice in one bundle
        compared a tree with itself."""
        honest = self.run_verdict(self.side("k", "left", binds=("fake_sides",)),
                                  self.side("r", "left", executor="fake_sides:sparse"))
        self.assertFalse(honest["held"])  #: compared, and the functions differ, which is honest
        with self.assertRaises(SystemExit):
            self.run_verdict(self.side("a", "left"), self.side("b", "left"))

    def test_a_cell_both_sides_refuse_is_listed_and_the_claim_is_about_what_both_ran(self):
        out = self.run_verdict(self.side("a", "left", executor="fake_sides:refuses_one", binds=("fake_sides",)),
                               self.side("b", "right", executor="fake_sides:refuses_one"))
        self.assertEqual((out["refused"], out["unusable"], out["compared"], out["held"]), ([ANTI], [], 1, True))
        #: a record carries no machine path: the binding names the file inside the bundle, a refusal its text
        self.assertNotIn(str(self.tree), json.dumps(out))
        self.assertEqual(out["sides"]["left"]["binding"], {"fake_sides": "fake_sides.py"})

    def test_a_comparison_that_compared_less_than_it_declared_is_a_refusal_not_a_pass(self):
        out = self.run_verdict(self.side("a", "left", names=(ANTI,)), self.side("b", "right", names=(ANTI,)),
                               minimum=99)
        self.assertEqual(out["quantities"], 2)
        self.assertFalse(out["held"])  #: every cell agreed, and it still does not pass
        self.assertIn("was declared to need 99", out["detail"])

    def test_the_per_slot_rule_reaches_the_verdict_and_a_per_quantity_rule_overrides_it(self):
        left = self.side("a", "left", params={"bump": 2e-3, "where": 0})
        #: y's slots are 1.0, so a 2e-3 bump is inside a 1% clause and outside a 1e-4 one
        loose = self.run_verdict(left, self.side("b", "right"), "per-slot", {"clauses": [[1e-2, 0.0]]})
        self.assertTrue(loose["held"])
        tight = self.run_verdict(left, self.side("b", "right"), "per-slot",
                                 {"clauses": [[1e-2, 0.0]], "per_quantity": {"y": {"clauses": [[1e-4, 0.0]]}}})
        self.assertFalse(tight["held"])
        self.assertTrue(tight["cells"][ANTI]["quantities"]["state"]["same"])  #: the default rule holds elsewhere
        self.assertEqual(tight["cells"][ANTI]["quantities"]["y"]["bound_by"], "clause (0.0001, 0.0)")


if __name__ == "__main__":
    unittest.main()
