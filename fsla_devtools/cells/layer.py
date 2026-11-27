"""LAYER CELLS: a sequence layer's input and how the layer is built to read it, as data -- the hidden states a layer's
projections read and the values it mixes, drawn from a seed, and the routing and logit gain its producer is built with.

A layer cell (`fsla_devtools.cells.form.Cell`, kind `layer`) is a batch of ``B`` sequences of ``tokens`` prefill tokens
(and ``decode_steps`` more, taken one at a time after the prefill), hidden states of width ``hidden`` and values of
``H`` write heads of width ``dv``, in one dtype; its ROUTING in FSLA's own terms (the topology, a write activation a
level, a read tuple a read head of its ``group``) and its logit ``gain``, the factor the layer's routing projection is
scaled by. How a package's layer is constructed from that is the package's (fsla's `measure.cells.layer`). ``seed`` is
the cell's: an arm that initializes parameters seeds them from it (`torch.manual_seed(seed)` immediately before
construction), and the inputs come from a device generator at ``seed + 500``, so a construction never moves them and
two cells that differ only in how the layer is built read the same tensors.
"""
from __future__ import annotations

from dataclasses import dataclass

#: the offset of the input generator's seed from the parameter seed
INPUT_SEED_OFFSET = 500


def check(cell) -> None:
    """A layer cell's own rules (`form.cell` calls it): a routing of one write activation a level and one read tuple
    (or one a read head of its group) of one entry a level, and a positive gain."""
    r = cell.routing
    if r is None or not r.topology or len(r.write) != r.D or len(r.read) not in (1, cell.group) \
            or any(len(head) != r.D for head in r.read):
        raise ValueError(f"a layer states one write activation a level and one read tuple (or one a read head of its "
                         f"group of {cell.group}) of one entry a level; got {r}")
    if cell.gain <= 0 or cell.decode_steps < 0:
        raise ValueError(f"a layer's gain is positive and its decode steps none or more; got {cell.gain}, "
                         f"{cell.decode_steps}")


@dataclass(frozen=True, slots=True)
class RealizedLayer:
    """A drawn layer cell: ``x`` ``[B, tokens + decode_steps, hidden]`` and ``v`` ``[B, tokens + decode_steps, H, dv]``."""

    #: the drawn cell (`form.Cell`, kind layer)
    cell: object
    x: object
    v: object

    @property
    def prefill(self):
        """``(x, v)`` over the prefill tokens alone, contiguous."""
        T = self.cell.tokens
        return self.x[:, :T].contiguous(), self.v[:, :T].contiguous()


def realize(cell, device: str = "cuda") -> RealizedLayer:
    import torch

    gen = torch.Generator(device=device).manual_seed(cell.seed + INPUT_SEED_OFFSET)
    total = cell.tokens + cell.decode_steps
    x = torch.randn((cell.B, total, cell.hidden_size), device=device, dtype=cell.torch_dtype, generator=gen)
    v = torch.randn((cell.B, total, cell.H, cell.dv), device=device, dtype=cell.torch_dtype, generator=gen)
    return RealizedLayer(cell, x, v)


__all__ = ["INPUT_SEED_OFFSET", "RealizedLayer", "check", "realize"]
