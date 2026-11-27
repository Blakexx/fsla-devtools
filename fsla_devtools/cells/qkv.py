"""QKV CELLS: attention's input as data -- queries, keys and values after their projections.

A QKV cell (`fsla_devtools.cells.form.Cell`, kind `qkv`) is ``B`` sequences of ``tokens`` tokens, ``H`` heads of width
``dv``, in ``dtype``, whether the problem is causal, and the seed of the device generator its queries, keys and values
are drawn from, in that order.
"""
from __future__ import annotations


def check(cell) -> None:
    """A QKV cell's own rules (`form.cell` calls it): none past the form's, which holds every field a QKV cell does not
    read at its default."""


def realize(cell, device: str = "cuda"):
    """``(q, k, v)``, each ``[B, H, tokens, dv]``."""
    import torch

    generator = torch.Generator(device=device).manual_seed(cell.seed)
    return tuple(torch.randn(cell.B, cell.H, cell.tokens, cell.dv, device=device, dtype=cell.torch_dtype,
                             generator=generator) for _ in range(3))


__all__ = ["check", "realize"]
