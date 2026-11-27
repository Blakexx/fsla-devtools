"""The verdict: the paired test, the persistence rule, one session's paired quantities and the three gates together. Every
check plants the violation its gate exists to catch and requires the gate to fire; a gate that cannot be shown failing is
treated as absent. Then the query, on a temporary backend of planted timing sessions: the reference is a label chosen
when reading, each other label's member of the same arm on the same cell is judged within its session, host drift both
arms share cancels, a unit is the code both members ran and the device they ran on, a member that failed, has no
reference or names no commit is not judged, and a pair of two levels is refused alone."""
from __future__ import annotations

import itertools
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest import mock

from test_results_index import isolate_cache

from fsla_devtools.results import index
from fsla_devtools.results import store as store_module
from fsla_devtools.results.store import Store
from fsla_devtools.results.verdict import (
    ALPHA,
    MIN_ROUNDS,
    _exact_two_sided_p,
    _normal_two_sided_p,
    _signed_rank_statistic,
    classify,
    paired_verdict,
    persistence,
    session,
    verdicts,
)


class PairedTest(unittest.TestCase):
    def test_a_planted_shift_is_detected_and_an_unshifted_pair_is_cleared(self):
        shifted = paired_verdict([0.05] * MIN_ROUNDS)
        self.assertEqual(shifted["verdict"], "b_slower")
        self.assertTrue(shifted["p"] <= ALPHA and shifted["exact"])
        self.assertEqual(paired_verdict([0.05, -0.05] * (MIN_ROUNDS // 2))["verdict"], "same")
        self.assertEqual(paired_verdict([-0.05] * MIN_ROUNDS)["verdict"], "b_faster")

    def test_the_round_floor_is_derived_from_alpha_and_is_binding(self):
        self.assertLessEqual(paired_verdict([0.05] * MIN_ROUNDS)["p"], ALPHA)
        short = [0.05] * (MIN_ROUNDS - 1)
        self.assertGreater(_exact_two_sided_p(*_signed_rank_statistic(short)), ALPHA)
        self.assertEqual(paired_verdict(short)["verdict"], "insufficient_data")

    def test_too_few_differences_after_the_ties_are_undecided_never_same(self):
        """A tied round is dropped: eight rounds with one tie leave seven differences, which can never reach the
        threshold, so the answer is that the design cannot decide -- not `same` (audit B4)."""
        one_tie = [0.05] * (MIN_ROUNDS - 1) + [0.0]
        self.assertGreater(_exact_two_sided_p(*_signed_rank_statistic(one_tie)), ALPHA)
        self.assertEqual(paired_verdict(one_tie)["verdict"], "insufficient_data")
        self.assertEqual(paired_verdict([0.0] * MIN_ROUNDS)["verdict"], "insufficient_data")

    def test_a_shift_smaller_than_the_noise_is_not_called(self):
        diffs = [0.01, -0.4, 0.35, -0.30, 0.28, -0.25, 0.22, -0.18, 0.30]
        self.assertEqual(paired_verdict(diffs)["verdict"], "same")

    def test_the_exact_and_approximate_tails_agree_where_they_meet(self):
        diffs = [0.3, -0.1, 0.25, 0.4, -0.05, 0.2, 0.35, 0.15, -0.2, 0.45, 0.05, 0.5, -0.15, 0.28, 0.33]
        w, ranks = _signed_rank_statistic(diffs)
        self.assertAlmostEqual(paired_verdict(diffs)["p"], _normal_two_sided_p(w, ranks), delta=0.02)


class Persistence(unittest.TestCase):
    def test_one_unlucky_session_is_not_a_regression_and_two_are(self):
        self.assertEqual(persistence([False, False, False, True]), "no_regression")
        self.assertEqual(persistence([False, True, True]), "regression")
        self.assertEqual(persistence([True, True]), "regression")

    def test_a_healed_run_is_suspicious_and_one_session_undecided(self):
        self.assertEqual(persistence([True, True, False]), "suspicious")
        self.assertEqual(persistence([True]), "insufficient_data")


def rounds(ms: float, n: int = MIN_ROUNDS, jitter: float = 0.01) -> list[list[float]]:
    """`n` rounds of three reps around `ms`, each round's reps spread by `jitter` and the rounds drifting together, as
    one session's host drift moves both arms."""
    return [[ms * (1 + 0.05 * (i % 3)) * f for f in (1 - jitter, 1, 1 + jitter)] for i in range(n)]


def paired(candidate_ms: float, reference_ms: float = 1.0, **kw) -> dict:
    return session(rounds(candidate_ms, **kw), rounds(reference_ms, **kw))


class OneSession(unittest.TestCase):
    def test_drift_both_arms_share_cancels_in_the_ratio(self):
        got = paired(1.0)
        self.assertEqual((got["ratio"], got["ratio_iqr"], got["violation"]), (1.0, 0.0, False))
        self.assertEqual(got["diffs_ms"], [0.0] * MIN_ROUNDS)

    def test_a_slower_candidate_violates_its_own_limit(self):
        got = paired(1.1)
        self.assertAlmostEqual(got["ratio"], 1.1)
        self.assertTrue(got["violation"])

    def test_a_spread_of_zero_is_floored_at_the_stopwatch_resolution(self):
        ticks = [[1.0, 1.0, 1.0625]] * MIN_ROUNDS
        got = session([[1.0625, 1.0625, 1.0]] * MIN_ROUNDS, ticks)
        self.assertEqual((got["ratio"], got["resolution"], got["floored_at_resolution"], got["violation"]),
                         (1.0625, 0.0625, True, False))
        self.assertTrue(session([[1.5, 1.5, 1.5625]] * MIN_ROUNDS, ticks)["violation"])

    def test_arms_that_show_no_resolution_cannot_resolve_the_pair(self):
        """Every call of each arm on one value: the stopwatch's tick is nowhere in the samples, so the gap between the
        arms is the floor and a one-tick difference is reported as unresolved, never a regression (audit B3)."""
        got = session([[0.0123] * 3] * MIN_ROUNDS, [[0.0118] * 3] * MIN_ROUNDS)
        self.assertEqual((got["resolved"], got["violation"]), (False, False))
        self.assertAlmostEqual(got["resolution"], (0.0123 - 0.0118) / 0.0118)
        self.assertEqual(classify([got, got])["verdict"], "cannot_resolve")

    def test_arms_of_different_rounds_or_an_unresolved_reference_are_refused(self):
        with self.assertRaises(ValueError):
            session(rounds(1.0, n=8), rounds(1.0, n=7))
        with self.assertRaises(ValueError):
            session(rounds(1.0), [[0.0, 0.0, 0.0]] * MIN_ROUNDS)


class ThreeGates(unittest.TestCase):
    def test_all_three_gates_must_fire(self):
        got = classify([paired(1.0), paired(1.6), paired(1.6)])
        self.assertEqual(got["verdict"], "regression")
        self.assertGreater(got["ratio"], got["limit"])
        self.assertEqual(got["significance"]["verdict"], "b_slower")

    def test_one_slow_session_is_flagged_not_confirmed(self):
        self.assertEqual(classify([paired(1.0), paired(1.6)])["verdict"], "flagged_not_confirmed")

    def test_an_effect_without_significance_is_flagged_not_confirmed(self):
        #: slower by a fifth on six rounds and faster by half on two: the median ratio is far over its limit, but the two
        #: large negative differences leave the paired signs insignificant
        reps = (0, 0.0001, 0.0002)
        mixed = session([[1.2 + e for e in reps]] * 6 + [[0.5 + e for e in reps]] * 2, [[1.0 + e for e in reps]] * 8)
        self.assertTrue(mixed["violation"])
        self.assertEqual(classify([mixed, mixed])["verdict"], "flagged_not_confirmed")

    def test_an_unchanged_kernel_passes_and_too_few_rounds_are_undecided(self):
        self.assertEqual(classify([paired(1.0), paired(1.0)])["verdict"], "no_regression")
        self.assertEqual(classify([paired(1.6, n=MIN_ROUNDS - 1)] * 2)["verdict"], "insufficient_data")

    def test_a_faster_candidate_is_no_regression(self):
        self.assertEqual(classify([paired(0.7), paired(0.7)])["verdict"], "no_regression")


ROUNDS = 8
REPS = 3


def plant(root: Path, *, tip_ms: float, master_ms: float = 1.0, tip_sha: str | None = "a", drift: float = 1.0,
          tip_owner: str = "tip/carry_forward", tip_level: str = "kernel", device: str = "gpu") -> None:
    """One stored session: tip and master timing `carry_forward` on `dense` with a host drift both share and a per-round
    swing, the attention reference on its own cell, and a tip registration that could not run."""
    members = [{"id": "m0", "owner": tip_owner, "cell": "dense", "status": "ok",
                "built": {"device": device, "level": tip_level}},
               {"id": "m1", "owner": "master/carry_forward", "cell": "dense", "status": "ok",
                "built": {"device": device, "level": "kernel"}},
               {"id": "m2", "owner": "bench/flash", "cell": "q", "status": "ok",
                "built": {"device": device, "level": "layer"}},
               {"id": "m3", "owner": "tip/prefill_op", "cell": "dense", "status": "failed", "error": "no prefill arm"}]
    samples, position = [], itertools.count()
    for rnd, rep in itertools.product(range(ROUNDS), range(REPS)):
        swing = drift * (1 + 0.05 * (rnd % 3)) * (1 + 0.01 * rep)
        for member, ms in (("m0", tip_ms), ("m1", master_ms), ("m2", 0.1)):
            samples.append({"member": member, "round": rnd, "rep": rep, "position": next(position) % 3, "ms": ms * swing})
    output = {"session": "session/G/carry_forward+flash", "run": "r", "instrument": "cuda_events", "rounds": ROUNDS,
              "reps": REPS, "clock": {"before": 1.665, "after": 1.665}, "members": members, "samples": samples}
    checkouts = {tip_owner: {"checkout": "tip", "git_sha": tip_sha, "diff_sha256": None},
                 "master/carry_forward": {"checkout": "master", "git_sha": "base", "diff_sha256": None},
                 "bench/flash": {"checkout": "fsla-bench", "git_sha": "bench", "diff_sha256": None}}
    store = Store("timing/session", root)
    #: a member with no commit is one the store stored before it refused such a sample: written around the refusal
    with mock.patch.object(store_module, "_uncommitted", lambda _p: None) if tip_sha is None else nullcontext():
        store.put({"executor": "measure", "cells": ["dense", "q"]}, output=output,
                  provenance={"run": "r", "checkouts": checkouts}, run="r")


class VerdictQuery(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        isolate_cache(self, Path(self.tmp.name) / "cache")
        self.root = Path(self.tmp.name) / "records"
        clock = (f"2026-09-15T00:{m:02d}:00Z" for m in itertools.count())
        patcher = mock.patch.object(store_module, "utc", lambda: next(clock))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_one_slow_session_is_flagged_and_a_second_at_the_same_code_confirms(self):
        plant(self.root, tip_ms=1.0)
        plant(self.root, tip_ms=1.0)
        self.assertEqual([r["verdict"] for r in verdicts(self.root, reference="master")], ["no_regression"])
        plant(self.root, tip_ms=1.6, tip_sha="slow")
        slow = [r for r in verdicts(self.root, reference="master") if r["git_sha"] == "slow"]
        self.assertEqual([(r["verdict"], r["n_sessions"]) for r in slow], [("flagged_not_confirmed", 1)])
        plant(self.root, tip_ms=1.6, tip_sha="slow")
        (got,) = [r for r in verdicts(self.root, reference="master") if r["git_sha"] == "slow"]
        self.assertEqual((got["verdict"], got["n_sessions"], got["arm"], got["cell"], got["candidate"]),
                         ("regression", 2, "carry_forward", "dense", "tip"))
        self.assertAlmostEqual(got["ratio"], 1.6)
        self.assertEqual(got["significance"]["verdict"], "b_slower")

    def test_host_drift_both_arms_share_is_no_regression(self):
        plant(self.root, tip_ms=1.0)
        plant(self.root, tip_ms=1.0, drift=1.3)
        (got,) = verdicts(self.root, reference="master")
        self.assertEqual((got["verdict"], got["ratio"], got["n_sessions"]), ("no_regression", 1.0, 2))

    def test_the_reference_is_the_readers_and_only_paired_members_are_judged(self):
        plant(self.root, tip_ms=1.6)
        (inverse,) = verdicts(self.root, reference="tip")
        self.assertEqual((inverse["candidate"], inverse["verdict"]), ("master", "no_regression"))
        self.assertAlmostEqual(inverse["ratio"], 1 / 1.6)
        self.assertEqual(verdicts(self.root, reference="someone-else"), [])
        self.assertEqual(verdicts(self.root, reference="master", arm="prefill_op"), [])

    def test_a_pair_of_two_levels_is_refused_alone_and_the_rest_are_judged(self):
        """One unlike pair is a refused row, not an exception that loses every other unit's verdict (audit C5)."""
        plant(self.root, tip_ms=1.0, tip_level="op")
        plant(self.root, tip_ms=1.0, tip_owner="next/carry_forward")
        got = {r["candidate"]: r for r in verdicts(self.root, reference="master")}
        self.assertEqual(got["tip"]["verdict"], "refused")
        self.assertIn("prices the op level", got["tip"]["reason"])
        self.assertEqual(got["next"]["verdict"], "no_regression")
        _, rows = index.query("SELECT DISTINCT label, arm, level FROM timing_members WHERE status = 'ok' ORDER BY label",
                              root=self.root)
        self.assertEqual(rows, [("bench", "flash", "layer"), ("master", "carry_forward", "kernel"),
                                ("next", "carry_forward", "kernel"), ("tip", "carry_forward", "op")])

    def test_two_devices_at_one_commit_are_two_units(self):
        """A card is part of what was measured: sessions on two devices are never judged as one unit (audit B9)."""
        plant(self.root, tip_ms=1.6, device="H100")
        plant(self.root, tip_ms=1.6, device="A100")
        got = verdicts(self.root, reference="master")
        self.assertEqual(sorted((r["device"], r["n_sessions"], r["verdict"]) for r in got),
                         [("A100", 1, "flagged_not_confirmed"), ("H100", 1, "flagged_not_confirmed")])

    def test_a_member_that_names_no_commit_is_not_judged(self):
        """What code an old commitless member timed is unknown, so it reaches no verdict (audit B8)."""
        plant(self.root, tip_ms=1.6, tip_sha=None)
        plant(self.root, tip_ms=1.6, tip_sha=None)
        self.assertEqual(verdicts(self.root, reference="master"), [])

    def test_a_members_label_is_its_owners_scope_and_its_arm_the_last_segment(self):
        plant(self.root, tip_ms=1.0, tip_owner="suite/tip/entmax_solve@layer=w16")
        _, rows = index.query("SELECT label, arm, git_sha FROM timing_members WHERE member = 'm0'", root=self.root)
        self.assertEqual(rows, [("suite/tip", "entmax_solve@layer=w16", "a")])
        _, rows = index.query("SELECT count(*), min(round), max(round), max(rep) FROM timing_samples WHERE member = 'm0'",
                              root=self.root)
        self.assertEqual(rows, [(ROUNDS * REPS, 0, ROUNDS - 1, REPS - 1)])

    def test_a_null_gates_cells_are_rows(self):
        cells = {"dense": {"trusted": True, "ratio_median": 1.0, "ratio_q1": 0.99, "ratio_q3": 1.01},
                 "sparse": {"trusted": None, "error": "a copy could not run"}}
        Store("timing/null", self.root).put({"executor": "null_gate"}, output={"members": [], "samples": [], "null": cells},
                                            provenance={"run": "r", "checkouts": {"tip": {"git_sha": "a"}}}, run="r")
        _, rows = index.query("SELECT cell, trusted, ratio_median, error FROM null_gates ORDER BY cell", root=self.root)
        self.assertEqual(rows, [("dense", 1, 1.0, None), ("sparse", None, None, "a copy could not run")])

    def test_an_instruments_cells_are_rows_with_their_status(self):
        summary = {"cells": {"dense": {"status": "ok", "exit": 0}, "deep": {"status": "failed", "exit": 1, "error": "no arm"}}}
        Store("fsla/phases", self.root).put({"executor": "run_tool"}, output={"dense": {}}, provenance={"run": "r"},
                                            run="r", summary=summary)
        _, rows = index.query("SELECT instrument, cell, status, error FROM instrument_cells ORDER BY cell", root=self.root)
        self.assertEqual(rows, [("phases", "deep", "failed", "no arm"), ("phases", "dense", "ok", None)])


if __name__ == "__main__":
    unittest.main()
