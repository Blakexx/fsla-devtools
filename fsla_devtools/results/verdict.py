"""THE VERDICT: whether a candidate's timing is a regression against a reference timed in the same sessions -- the
statistics, and the query that reads them off the stored sessions.

    python -m fsla_devtools.results verdict --reference LABEL [--candidate LABEL] [--cell C] [--arm A] [--json]

Timing is comparable only within the session that interleaved it, so every gate reads within-session quantities.
:func:`session` turns one session's two arms -- the candidate's and the reference's samples, grouped by round -- into the
paired quantities: per round each arm's median, their RATIO (candidate over reference) and their DIFFERENCE. Three gates,
and a regression needs all three:

1. EFFECT SIZE -- the session's median ratio above :func:`ratio_limit`: one plus three sigmas of the per-round ratios'
   own spread (sigma from the interquartile range), never of a spread smaller than the stopwatch can resolve
   (:func:`resolution`, relative to the reference). Never a flat percentage. Where neither arm shows the stopwatch's
   resolution (every call of each arm on one value), the session CANNOT RESOLVE the pair: the gap between the arms is
   the only floor left, and the session is reported as `cannot_resolve`, never judged.
2. SIGNIFICANCE -- :func:`paired_verdict`: an exact Wilcoxon signed-rank test over the session's per-round differences.
   Paired, because the interleaving paid for the pairing; exact, because the round counts are single digits.
3. PERSISTENCE -- :func:`persistence` over the candidate's sessions against the same reference, in order: a regression
   is a trailing run of at least two sessions over their own limits. One unlucky session is not one.

Anything short of all three is reported as what it is (`flagged_not_confirmed`, `suspicious`, `insufficient_data`,
`cannot_resolve`, `no_regression`), never as a pass.

THE QUERY. The reference is the reader's choice, never the run's. In every stored timing session (`timing_members`,
`timing_samples`) that timed the reference label's arm on a cell, each other label's member of that arm on that cell is a
candidate, and the pair is judged within the session. A UNIT is (arm, cell, candidate label, reference label), the
device both ran on, and the code both members ran (each checkout's commit and uncommitted diff): :func:`classify` reads a
unit's sessions oldest first and judges the newest. A member whose provenance names no commit is not read: what code it
timed is unknown. A pair whose two members price different LEVELS (`kernel`, `op`, `layer`) is refused, alone, and
reported as refused: a kernel's launch and a layer's whole call are different quantities, and a comparison between two
libraries is a comparison of their layers. Nothing is stored: a verdict is a reading of the records, taken again
whenever it is asked for.
"""
from __future__ import annotations

import math
import statistics
from itertools import product
from pathlib import Path

from . import index

#: The paired test's alpha, stated once.
ALPHA = 0.01
#: DERIVED FROM ALPHA, not chosen: the smallest two-sided exact p reachable with n non-zero differences is 2 / 2**n (every
#: sign the same way), so below ceil(log2(2 / ALPHA)) non-zero differences no outcome can be significant and the test
#: cannot decide.
MIN_ROUNDS = math.ceil(math.log2(2 / ALPHA))
#: Above this many non-zero differences the exact enumeration's 2**n terms give way to the normal approximation, which is
#: accurate there to well past the third decimal of p.
EXACT_MAX_N = 20
#: The one free choice of the effect-size gate: a one-sided three-sigma alarm, about 0.1 % false alarms per cell per session.
ALARM_SIGMA = 3.0
#: Q3 - Q1 of a normal sample, in sigmas: what turns a measured interquartile range into a sigma.
IQR_PER_SIGMA = 1.349


def iqr(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[int(0.75 * len(ordered))] - ordered[int(0.25 * len(ordered))]


def _gaps(values: list[float]) -> list[float]:
    distinct = sorted(set(values))
    return [b - a for a, b in zip(distinct, distinct[1:], strict=False)]


def resolution(arms: list[list[float]]) -> float | None:
    """The stopwatch's resolution as the samples themselves show it: the smallest gap between two distinct values WITHIN
    one arm's samples, None where no arm has two. A device-event timer quantizes, so a small kernel's calls can land on
    one value every time; the per-round ratios' spread is then zero, a limit at the median ratio, and the next tick over
    it. Within an arm, never across two: a gap between the arms is the effect being judged, not the stopwatch. Measured
    on every judgement rather than stored, because it is a property of the stopwatch and the box, and a stored copy could
    disagree with the samples."""
    return min((gap for samples in arms for gap in _gaps(samples)), default=None)


def ratio_limit(ratio_iqr: float) -> float:
    """The ratio above which a candidate is over the line: one plus three sigmas of the per-round ratios' own spread."""
    return 1.0 + ALARM_SIGMA * ratio_iqr / IQR_PER_SIGMA


def _signed_rank_statistic(diffs: list[float]) -> tuple[float, list[float]]:
    """W+ (the sum of the ranks of the positive differences) and the ranks. Zero differences are discarded, Wilcoxon's
    own treatment; ties among the absolute differences take mid-ranks."""
    nonzero = [d for d in diffs if d != 0]
    order = sorted(range(len(nonzero)), key=lambda i: abs(nonzero[i]))
    ranks = [0.0] * len(nonzero)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and abs(nonzero[order[j + 1]]) == abs(nonzero[order[i]]):
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return sum(r for r, d in zip(ranks, nonzero, strict=True) if d > 0), ranks


def _exact_two_sided_p(w_plus: float, ranks: list[float]) -> float:
    """P(|W+ - mean| >= |observed - mean|) with each sign a fair coin, enumerated over the actual ranks (ties included)."""
    mean = sum(ranks) / 2
    observed = abs(w_plus - mean)
    hits = sum(abs(sum(r for r, take in zip(ranks, signs, strict=True) if take) - mean) >= observed - 1e-12
               for signs in product((0, 1), repeat=len(ranks)))
    return hits / 2 ** len(ranks)


def _normal_two_sided_p(w_plus: float, ranks: list[float]) -> float:
    """The large-sample approximation, with the tie correction the mid-ranks require."""
    n = len(ranks)
    counts: dict[float, int] = {}
    for rank in ranks:
        counts[rank] = counts.get(rank, 0) + 1
    var = n * (n + 1) * (2 * n + 1) / 24 - sum(t ** 3 - t for t in counts.values()) / 48
    if var <= 0:
        return 1.0
    return math.erfc(abs((w_plus - n * (n + 1) / 4) / math.sqrt(var)) / math.sqrt(2))


def paired_verdict(diffs: list[float], *, alpha: float = ALPHA) -> dict:
    """Whether per-round differences (candidate minus baseline) are distinguishable from zero.

    `verdict` is `insufficient_data` (fewer than MIN_ROUNDS non-zero differences: the design cannot decide -- a tied
    round is dropped, so 8 rounds with one tie cannot reach the threshold either), `same`, `b_slower` or `b_faster`,
    with the p-value and the n it was computed on.
    """
    diffs = list(diffs)
    w_plus, ranks = _signed_rank_statistic(diffs)
    if len(ranks) < MIN_ROUNDS:
        return {"verdict": "insufficient_data", "p": None, "n": len(ranks),
                "reason": f"{len(ranks)} non-zero differences of {len(diffs)} rounds < MIN_ROUNDS={MIN_ROUNDS}"}
    exact = len(ranks) <= EXACT_MAX_N
    p = _exact_two_sided_p(w_plus, ranks) if exact else _normal_two_sided_p(w_plus, ranks)
    verdict = "same" if p > alpha else ("b_slower" if sum(diffs) > 0 else "b_faster")
    return {"verdict": verdict, "p": p, "n": len(ranks), "w_plus": w_plus, "exact": exact}


def persistence(violations: list[bool]) -> str:
    """The persistence rule over one candidate's sessions against one reference, oldest first, the last the one judged:
    `regression` for a trailing run of at least two violations, `suspicious` for a run of at least two that has stopped,
    `insufficient_data` for a single session, otherwise `no_regression`."""
    flags = [bool(v) for v in violations]
    if len(flags) < 2:
        return "insufficient_data"
    trailing = 0
    for flag in reversed(flags):
        if not flag:
            break
        trailing += 1
    if trailing >= 2:
        return "regression"
    run = best = 0
    for flag in flags:
        run = run + 1 if flag else 0
        best = max(best, run)
    return "suspicious" if best >= 2 else "no_regression"


def session(candidate: list[list[float]], reference: list[list[float]]) -> dict:
    """ONE SESSION'S PAIRED QUANTITIES from the candidate's and the reference's samples in milliseconds, each a list of
    rounds of reps, from one interleaved session. Refuses arms of different round counts and a reference round whose
    median is zero (it has no ratio). `resolved` is whether an arm showed the stopwatch's resolution; where none did,
    the floor is the smallest gap between the two arms' values, and :func:`classify` reports the pair unresolved."""
    if not candidate or len(candidate) != len(reference):
        raise ValueError(f"paired arms need the same rounds, got {len(candidate)} and {len(reference)}")
    c = [statistics.median(r) for r in candidate]
    b = [statistics.median(r) for r in reference]
    if min(b) <= 0:
        raise ValueError("a reference round's median is zero: the stopwatch did not resolve it, and it has no ratio")
    ratios = [x / y for x, y in zip(c, b, strict=True)]
    spread = iqr(ratios)
    flat_c, flat_b = [v for r in candidate for v in r], [v for r in reference for v in r]
    within = resolution([flat_c, flat_b])
    floor = within if within is not None else min(_gaps(flat_c + flat_b), default=0.0)
    tick = floor / statistics.median(b)
    limit = ratio_limit(max(spread, tick))
    ratio = statistics.median(ratios)
    return {"ratio": ratio, "limit": limit, "ratio_iqr": spread, "resolution": tick, "resolved": within is not None,
            "floored_at_resolution": tick > spread, "violation": ratio > limit,
            "diffs_ms": [x - y for x, y in zip(c, b, strict=True)], "rounds": len(c),
            "candidate_ms": statistics.median(c), "reference_ms": statistics.median(b)}


def classify(sessions: list[dict]) -> dict:
    """THE FLAGGING RULE: `sessions` are one candidate's sessions against one reference (each :func:`session`'s
    quantities), oldest first, the last the one judged."""
    if not sessions:
        raise ValueError("no session to judge")
    last = sessions[-1]
    significance = paired_verdict(last["diffs_ms"])
    lasting = persistence([s["violation"] for s in sessions])
    if last["rounds"] < MIN_ROUNDS:
        verdict = "insufficient_data"
    elif not last["resolved"]:
        verdict = "cannot_resolve"
    elif last["violation"]:
        confirmed = significance["verdict"] == "b_slower" and lasting == "regression"
        verdict = "regression" if confirmed else "flagged_not_confirmed"
    else:
        verdict = "suspicious" if lasting == "suspicious" else "no_regression"
    return {"verdict": verdict, **{k: v for k, v in last.items() if k not in ("violation", "diffs_ms")},
            "n_sessions": len(sessions), "persistence": lasting, "significance": significance}


_MEMBERS = """SELECT target, run, utc, member, label, arm, cell, level, git_sha, diff_sha256, device
              FROM timing_members WHERE status = 'ok' AND git_sha IS NOT NULL ORDER BY utc, target, run"""
_SAMPLES = "SELECT target, run, member, round, ms FROM timing_samples"


def verdicts(root: Path | str | None = None, *, reference: str, candidate: str | None = None, cell: str | None = None,
             arm: str | None = None) -> list[dict]:
    """Every unit's verdict against `reference`, in the order of their newest sessions; a refused pair is a row of its
    own (`verdict` `refused`, its `reason`), and the rest are judged."""
    columns, rows = index.query(_MEMBERS, root=root)
    members = [dict(zip(columns, row, strict=True)) for row in rows]
    rounds: dict[tuple, dict[int, list[float]]] = {}
    for target, run, member, rnd, ms in index.query(_SAMPLES, root=root)[1]:
        rounds.setdefault((target, run, member), {}).setdefault(rnd, []).append(ms)

    stored: dict[tuple, list[dict]] = {}
    for m in members:
        stored.setdefault((m["target"], m["run"]), []).append(m)
    units: dict[tuple, list[tuple[dict, dict]]] = {}
    refused: dict[tuple, dict] = {}
    for sample, ms in stored.items():
        refs = {(m["arm"], m["cell"]): m for m in ms if m["label"] == reference}
        for m in ms:
            base = refs.get((m["arm"], m["cell"]))
            if base is None or m is base or (candidate is not None and m["label"] != candidate) \
                    or (cell is not None and m["cell"] != cell) or (arm is not None and m["arm"] != arm):
                continue
            c, b = rounds.get((*sample, m["member"]), {}), rounds.get((*sample, base["member"]), {})
            if not c or sorted(c) != sorted(b):
                continue
            unit = (m["arm"], m["cell"], m["label"], m["level"], m["device"], m["git_sha"], m["diff_sha256"],
                    base["git_sha"], base["diff_sha256"])
            #: A PAIR IS ONE LEVEL (Blake, 2026-09-15): a kernel's launch and a layer's whole call are different
            #: quantities, so a ratio between them is refused here rather than reported as a comparison
            if m["level"] != base["level"]:
                refused[unit] = {**_unit_row(unit, reference), "utc": m["utc"], "verdict": "refused",
                                 "reason": f"{m['label']}'s {m['arm']} prices the {m['level']} level and {reference}'s "
                                           f"prices the {base['level']} level on {m['cell']}: a verdict compares one level"}
                continue
            units.setdefault(unit, []).append((m, session([c[r] for r in sorted(c)], [b[r] for r in sorted(b)])))
    out = list(refused.values())
    for unit, judged in units.items():
        out.append({**_unit_row(unit, reference), "utc": judged[-1][0]["utc"],
                    **classify([paired for _m, paired in judged])})
    return sorted(out, key=lambda r: (r["utc"], r["arm"], r["cell"], r["candidate"]))


def _unit_row(unit: tuple, reference: str) -> dict:
    arm, cell, label, level, device, sha, diff, base_sha, base_diff = unit
    return {"arm": arm, "cell": cell, "candidate": label, "level": level, "device": device, "git_sha": sha,
            "diff_sha256": diff, "reference": reference, "reference_git_sha": base_sha, "reference_diff_sha256": base_diff}


def table(rows: list[dict]) -> str:
    head = f"{'verdict':<22}{'unit':<56}{'candidate':<20}{'ratio':>8}{'limit':>8}{'p':>9}  sessions  persistence"
    lines = [head]
    for r in rows:
        unit, candidate = f"{r['arm']}@{r['cell']}", f"{r['candidate']} {(r['git_sha'] or '')[:8]}"
        if r["verdict"] == "refused":
            lines.append(f"{r['verdict']:<22}{unit:<56}{candidate:<20}  {r['reason']}")
            continue
        p = r["significance"].get("p")
        lines.append(f"{r['verdict']:<22}{unit:<56}{candidate:<20}"
                     f"{r['ratio']:>8.3f}{r['limit']:>8.3f}{('-' if p is None else f'{p:.4f}'):>9}  "
                     f"{r['n_sessions']:>8}  {r['persistence']}")
    return "\n".join(lines)
