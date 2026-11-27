"""THE CELL NODE'S EXECUTOR: one cell's description (`fsla_devtools.cells.descriptions`) -- its record, with the digest
of the code that draws it.

It runs in the build system's own environment and reads this package, so a target that takes the cell keys on its record
AND on the drawing code: change a draw and every result over that cell re-keys, instead of a new sample landing on the
old record. The record is a DESCRIPTION -- the provider, the parameters and the seed -- and never the tensors: those are
drawn in the worker that times them, from the seed the record states, and each worker reports the digest back so a
checkout drawing with other code fails its entry instead of being compared.
"""
from __future__ import annotations

from . import central, draw_digest


def record(ctx) -> dict:
    return {**central().cell(ctx.params["cell"]), "draw": draw_digest()}


__all__ = ["record"]
