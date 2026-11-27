"""THE DIFF'S VERDICT: two sides produced, each on its own bundle's interpreter, and compared cell by cell and quantity
by quantity under one rule (`strategies.py`); and a diff's gate over that verdict.

    TOOL --left DESC --right DESC --strategy S [--params JSON] [--expect same|different] [--minimum N]
         [--threads N] [--environment NAME] --json OUT                         # the verdict, an instrument's tool
    python -m fsla_devtools.diff.verdict --check --runfiles DIR --spec PATH    # a `diff_test`

`bazel/measure.bzl`'s `diff_verdict` is an instrument whose tool is this: it runs in the instrument's tree, where both
sides' runfiles are laid out, starts `fsla_devtools.diff.side` for each side on the side's bundle's interpreter (the
tensors go to a scratch directory that never outlives the run), then compares. Over host sides it is the rule's action,
seeing no device; over a side on the device it runs in the run phase of its store target, or in its test, and takes
the GPU exclusive and the host budget whole first.

THE CLAIM. `expect` "same" is a gate on an invariant (a refactor that changed no number, a kernel inside its oracle's
band), "different" the non-vacuity half -- a planted difference that must be seen. A cell either side failed to
produce is NOT a comparison that passed: it is the cell's own status and a difference the claim answers for; a cell
BOTH sides refused is listed and not counted. `minimum` is how many quantities must have been compared for the verdict
to mean anything: short of it the claim is not held. Two sides with the same bundle and executor compared one tree
with itself and are refused. The verdict JSON always says whether the claim `held`; what a broken claim is -- a failed
gate or a recorded outcome -- is the gate's (`diff_test`'s `on_difference`), and a store target records the verdict.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .strategies import compare as apply_strategy


def _side(description: Path, tree: Path, out: Path, threads: int) -> dict:
    d = json.loads(description.read_text())
    root = tree / d["bundle"]["root"]
    env = {**os.environ, "PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1"}
    done = subprocess.run([d["bundle"]["python"], "-m", "fsla_devtools.diff.side", "--description",
                           str(description.absolute()), "--tree", str(tree.absolute()), "--out", str(out),
                           "--threads", str(threads)], cwd=root, env=env, check=False)
    if done.returncode:
        raise SystemExit(f"the side {d['owner']} exited {done.returncode}")
    side = json.loads((out / "side.json").read_text())
    side["digest"] = json.loads((tree / d["bundle"]["manifest"]).read_text())["digest"]
    side["device"] = bool(d.get("device"))
    side["dir"] = out
    return side


def _load(side: dict, name: str) -> dict:
    import torch

    return torch.load(side["dir"] / side["cells"][name]["file"], map_location="cpu", weights_only=True)


def compare(left: dict, right: dict, *, strategy: str, params: dict, expect: str, minimum: int) -> dict:
    """The two sides' cells, compared; the claim, and whether it held."""
    if left["digest"] == right["digest"] and left["executor"] == right["executor"]:
        raise SystemExit(f"both sides ran {left['executor']} in one bundle ({left['digest'][:12]}): this comparison ran "
                         "one tree against itself and its verdict means nothing")
    cells, differing, unusable, refused = {}, [], [], []
    for name in sorted(set(left["cells"]) | set(right["cells"])):
        sides = {"left": left["cells"].get(name), "right": right["cells"].get(name)}
        missing = [s for s, state in sides.items() if state is None or state["status"] != "ok"]
        if len(missing) == 2 and all(sides[s] is not None for s in missing):
            cells[name] = {"status": "refused", "left": sides["left"].get("detail"), "right": sides["right"].get("detail")}
            refused.append(name)
            continue
        if missing:
            detail = {s: (sides[s] or {}).get("detail", "no such cell on this side") for s in missing}
            cells[name] = {"status": "unusable", "produced_by": [s for s in sides if s not in missing], **detail}
            unusable.append(name)
            continue
        if sides["left"]["draw"] != sides["right"]["draw"]:
            cells[name] = {"status": "unusable", "why": "the two sides drew this cell with different code",
                           "left_draw": sides["left"]["draw"], "right_draw": sides["right"]["draw"]}
            unusable.append(name)
            continue
        a, b = _load(left, name), _load(right, name)
        #: a quantity named as another's ENVELOPE is a bound the reference side supplies, never a thing compared
        envelopes = {params.get("envelope_from")} | {q.get("envelope_from") for q in params.get("per_quantity", {}).values()}
        verdicts = {}
        for quantity in sorted((set(a) | set(b)) - envelopes):
            if quantity not in a or quantity not in b:
                verdicts[quantity] = {"same": False, "why": f"only {'left' if quantity in a else 'right'} produced it"}
                continue
            rule = {**params, **params.get("per_quantity", {}).get(quantity, {})}
            rule.pop("per_quantity", None)
            envelope = rule.pop("envelope_from", None)
            if envelope is not None:
                rule["envelope"] = b[envelope]
            verdicts[quantity] = apply_strategy(strategy, a[quantity], b[quantity], rule)
        same = all(v.get("same") for v in verdicts.values())
        cells[name] = {"status": "ok", "same": same, "quantities": verdicts}
        if not same:
            differing.append(name)
    quantities = sum(len(c.get("quantities", {})) for c in cells.values())
    held = (not differing and not unusable) if expect == "same" else (bool(differing) and not unusable)
    out = {"strategy": strategy, "expect": expect, "cells": cells, "differing": differing, "unusable": unusable,
           "refused": refused, "compared": len(cells) - len(unusable) - len(refused), "quantities": quantities,
           "minimum": minimum, "held": held and quantities >= minimum,
           "sides": {s: {"owner": side["owner"], "bundle": side["digest"], "binding": side["binding"]}
                     for s, side in (("left", left), ("right", right))}}
    if not out["held"]:
        out["detail"] = message(out)
    return out


def message(out: dict) -> str:
    if out["unusable"]:
        return (f"{len(out['unusable'])} of {len(out['cells'])} cells produced no comparison "
                f"({', '.join(out['unusable'][:4])}): a gate that goes green on a cell neither side produced is a gate "
                f"that measured nothing")
    if out["quantities"] < out["minimum"]:
        return (f"this comparison compared {out['quantities']} quantities and was declared to need {out['minimum']}: a "
                "gate that cannot run says so, it does not report a verdict over whatever survived")
    if out["expect"] == "same":
        worst = max((q for name in out["differing"] for q in out["cells"][name]["quantities"].values()
                     if not q.get("same")), key=lambda q: q.get("ratio", 0.0), default={})
        return (f"{len(out['differing'])} of {out['compared']} cells differ under {out['strategy']} "
                f"({', '.join(out['differing'][:4])}); the worst slot is {json.dumps(worst, sort_keys=True)}")
    return (f"every one of {out['compared']} cells is the same under {out['strategy']}, and a difference was EXPECTED: "
            "the comparison cannot see what it was built to see")


def verdict(left: Path, right: Path, *, strategy: str, params: dict, expect: str, minimum: int, threads: int,
            tree: Path = Path(".")) -> dict:
    """Both sides produced into a scratch directory of this run, then compared; over a side on the device, under the
    GPU lock and the whole host budget."""
    devices = any(json.loads(p.read_text()).get("device") for p in (left, right))
    with contextlib.ExitStack() as held, tempfile.TemporaryDirectory(prefix="diff-") as scratch:
        if devices:
            from ..locks import host
            from ..locks.gpu import gpu_lock

            held.enter_context(gpu_lock(mode="exclusive"))
            held.enter_context(host.acquire(exclusive=True, label="a diff over a side on the device"))
        sides = [_side(p, tree, Path(scratch) / name, threads) for p, name in ((left, "left"), (right, "right"))]
        return compare(*sides, strategy=strategy, params=params, expect=expect, minimum=minimum)


def check(runfiles: Path, spec: dict) -> int:
    """A diff's gate: the verdict read (or, over a side on the device, run now), its claim judged."""
    from ..instrument import run

    if spec["output"]:
        out = json.loads((runfiles / spec["output"]).read_text())
    else:
        with tempfile.TemporaryDirectory(prefix="diff-test-") as scratch:
            files = [(path, str(runfiles / source)) for path, source in spec["files"]]
            out = run(files, spec["tool"], spec["args"], Path(scratch) / "verdict.json", python=sys.executable,
                      device=True)
    if out["held"]:
        print(f"{spec['verdict']}: held ({out['compared']} cells, {out['quantities']} quantities)")
        return 0
    print(f"{spec['verdict']}: NOT HELD -- {out['detail']}")
    return 1 if spec["on_difference"] == "fail" else 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m fsla_devtools.diff.verdict")
    ap.add_argument("--check", action="store_true", help="a diff's gate over its verdict (`--runfiles`, `--spec`)")
    ap.add_argument("--runfiles", type=Path)
    ap.add_argument("--spec", help="the gate's spec, by its path in the runfiles")
    ap.add_argument("--left", type=Path)
    ap.add_argument("--right", type=Path)
    ap.add_argument("--strategy")
    ap.add_argument("--params", default="{}")
    ap.add_argument("--expect", default="same", choices=("same", "different"))
    ap.add_argument("--minimum", type=int, default=1)
    ap.add_argument("--threads", type=int, default=1)
    ap.add_argument("--environment", help="the comparison's environment, named so it keys the record")
    ap.add_argument("--json", type=Path)
    a = ap.parse_args(argv)
    if a.check:
        runfiles = a.runfiles.absolute()
        return check(runfiles, json.loads((runfiles / a.spec).read_text()))
    out = verdict(a.left, a.right, strategy=a.strategy, params=json.loads(a.params), expect=a.expect, minimum=a.minimum,
                  threads=a.threads)
    a.json.write_text(json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))


__all__ = ["check", "compare", "main", "message", "verdict"]
