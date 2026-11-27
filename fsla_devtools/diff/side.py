"""ONE DIFF SIDE, produced: a side's executor over its cells, in its bundle, its tensors written to a directory.

    python -m fsla_devtools.diff.side --description PATH --tree DIR --out DIR --threads N

The comparison program (`fsla_devtools.diff.verdict`) starts this on the side's bundle's interpreter, in the bundle's
root with the root as its import path, so the side's code is the bundle's and the drawing code its environment's. The
executor (`module:function`) is called with each cell's description (its record and its draw digest) and the side's
parameters and returns `{name: tensor}`: each cell's quantities go to `<cell>.pt`, and `side.json` says per cell what
was produced (its draw digest, each quantity's shape and dtype) or why not -- a cell whose call raises is that cell's
domain failure and the rest still run. `binds` NAMES THE LIBRARY THE SIDE IS SUPPOSED TO BE RUNNING: each module must
load from inside the bundle, or the side fails, since a side that imported another tree would compare that tree with
itself.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path


def _load(ref: str):
    module, _, name = ref.partition(":")
    if not module or not name:
        raise ValueError(f"an executor is module:function, got {ref!r}")
    return getattr(importlib.import_module(module), name)


def binding(binds: list[str], root: Path) -> dict[str, str]:
    """Each bound module's file, inside the bundle's root, or the side fails."""
    out = {}
    for module in binds:
        where = Path(importlib.import_module(module).__file__)
        resolved = Path(where.parent.resolve(), where.name)
        if not resolved.is_relative_to(root.resolve()):
            raise SystemExit(f"this side runs in its bundle and imported {module} from {where}, outside it: the "
                             "comparison would have run another tree")
        out[module] = resolved.relative_to(root.resolve()).as_posix()
    return out


def produce(description: dict, tree: Path, out: Path, threads: int) -> dict:
    import torch

    torch.set_num_threads(max(1, threads))
    root = tree / description["bundle"]["root"]
    subject = _load(description["executor"])
    bound = binding(description["binds"], root)
    portable = str(tree.resolve()) + "/"
    cells = {}
    for path in description["cells"]:
        record = json.loads((tree / path).read_text())
        name = record["name"]
        try:
            quantities = subject(record, **description["params"])
        except Exception as ex:  # noqa: BLE001 -- the cell's domain failure, recorded and carried
            cells[name] = {"status": "failed", "detail": f"{type(ex).__name__}: {ex}".replace(portable, "")}
            continue
        if not isinstance(quantities, dict) or not quantities:
            raise TypeError(f"{description['executor']} returned {type(quantities).__name__} for {name}; a side returns "
                            "a non-empty {name: tensor}")
        torch.save({k: v.detach().cpu() for k, v in quantities.items()}, out / f"{name}.pt")
        cells[name] = {"status": "ok", "file": f"{name}.pt", "draw": record.get("draw"),
                       "quantities": {k: {"shape": list(v.shape), "dtype": str(v.dtype)} for k, v in quantities.items()}}
    return {"owner": description["owner"], "executor": description["executor"], "binding": bound, "cells": cells}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m fsla_devtools.diff.side")
    ap.add_argument("--description", type=Path, required=True, help="the side's description, in the tree")
    ap.add_argument("--tree", type=Path, required=True, help="the runfiles tree the description's paths are in")
    ap.add_argument("--out", type=Path, required=True, help="the directory its tensors and side.json go to")
    ap.add_argument("--threads", type=int, default=1, help="torch's threads")
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    side = produce(json.loads(a.description.read_text()), a.tree, a.out, a.threads)
    (a.out / "side.json").write_text(json.dumps(side, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))


__all__ = ["binding", "main", "produce"]
