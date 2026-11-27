"""PRODUCER CELLS: the routing producer's input as data -- one level's logits at a stated logit GAIN, a mask, and the
activation they are solved at.

The producer is the step that turns logits into routing amplitudes on the simplex (entmax at its alpha), and the thing
that distinguishes two implementations of it is not the amplitudes in the middle of the distribution but WHERE THE
SUPPORT ENDS. A cell (`fsla_devtools.cells.form.Cell`, kind `producer`) therefore declares its `gain`, the scale its
logits are drawn at, because the gain is the axis that moves the boundary:

    0.5   a nearly dense simplex -- almost every digit survives, and a support disagreement is nearly impossible
    3     the working regime
    30    where a bisection's finite iteration count shows, and where two implementations are likeliest to disagree
          about which digits survive at all

`masked` is a RAGGED mask, never a uniform one: padding past a level's logical width is what a kernel's mask does, and a
uniform mask exercises none of the boundary the mask exists for.

Symbols, each at first use: the cell's routing is ONE level, `width` digits (its topology) under one write activation,
`entmax<alpha>` (1.5 is the sparse producer, 2 is sparsemax); `tokens` = the independent logit rows drawn.

Standard library until a draw is realized: `realize` imports torch where it is called.
"""
from __future__ import annotations

#: the alphas a shipped producer solves at
ALPHAS = (1.5, 2.0)


def alpha(cell) -> float:
    """The alpha the cell's one level is solved at."""
    from .form import entry

    return entry(cell.routing.write[0])[1]


def check(cell) -> None:
    """A producer cell's own rules (`form.cell` calls it): one level of at least one digit, one write activation a
    shipped producer solves, no read side."""
    r = cell.routing
    if r is None or r.D != 1 or len(r.write) != 1 or r.read:
        raise ValueError(f"a producer cell is one level under one write activation, with no read side; got {r}")
    if r.topology[0] < 1:
        raise ValueError("a producer cell draws at least one digit")
    if alpha(cell) not in ALPHAS:
        raise ValueError(f"alpha {alpha(cell)} is not one a shipped producer solves at {ALPHAS}")


def realize(cell, device: str = "cpu"):
    """`(logits, mask)`: the cell's logit rows and its mask, or `None` where it declares none.

    fp64 and on the CPU by default: the producer's reference is a pure-torch fp64 object, and what is being compared
    is arithmetic rather than a launch.
    """
    import torch

    width = cell.routing.topology[0]
    shape = (cell.tokens, width)
    gen = torch.Generator(device=device).manual_seed(cell.seed)
    draw = lambda: torch.randn(shape, generator=gen, dtype=torch.float64, device=device) * cell.gain  # noqa: E731
    mask = None
    if cell.masked:
        mask = torch.ones(shape, dtype=torch.bool, device=device)
        #: RAGGED, a different live width per row: a uniform mask is a narrower cell, not a masked one
        flat = mask.reshape(-1, width)
        for row in range(flat.shape[0]):
            flat[row, max(1, width - row % width):] = False
    return draw(), mask


__all__ = ["ALPHAS", "alpha", "check", "realize"]
