"""CARRY CELLS: a sparse recurrence's input as data -- per-level routing amplitudes on the simplex for both sides, a gain,
the values, and the state the sequence enters with -- with one draw behind each record.

`carry.json` holds the records (`fsla_devtools.cells.form.Cell`, kind `carry`); `check` holds one to a carry cell's
rules, and `realize` draws it: ``[B, L, H, B_l]`` read and write amplitudes per level, a ``[B, L, H]`` gain and
``[B, L, H, DV]`` values, in bf16, plus the entry state of a carried cell. A record declares its ROUTING (the topology
and what each side draws on each level), its DRAW (the support structure), a shape and the STATE it binds -- which
backing (`dense` or `paged`) and whether it enters empty (`fresh`), carries a drawn state (`carried`) or binds none --
and never how a kernel runs it: a launch shape, a window grid or a stream count is the arm's, so two kernels timed on
one cell read the same tensors.

Every cell but the two degenerate ones states where in the routing distribution it sits (`regime`, on every axis of
`fsla_devtools.cells.regimes.REGIME_AXES`), and `realize` proves it before any reader sees the draw.

Symbols, each at first use: ``D`` = routing depth, ``B_l`` = level ``l``'s digit count, ``N = prod_l B_l`` = leaf
capacity, ``DV`` = the value width, ``L`` = tokens, ``BH`` = batch times heads, ``k_tok`` = a token's nonzero digits
on a sparse level (a `k<n>` entry: a property of the draw, never told to a kernel).

Standard library until a draw is realized: `realize` imports torch where it is called.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

#: the support structures a carry draw takes: `iid` (each level-side its routing entry's draw, every token alone),
#: `cohort` (runs of `cohort` tokens share one digit window a sparse level), the CORNERS and the DEGENERATE draws
CORNERS = ("anti", "cold", "readonly", "concentrated", "onehot", "flip")
#: the draws with no support to sample, which state it themselves and declare no regime
DEGENERATE = ("dead", "deposit")
DRAWS = ("iid", "cohort", *CORNERS, *DEGENERATE)
BACKINGS = ("dense", "paged")
STATES = ("none", "fresh", "carried")
#: who a cell is sized for: `oracle`, small enough for an fp64 reference; `probe`, a measurement's size; `both`
TIERS = ("oracle", "probe", "both")
#: THE TOKEN WINDOW the `tail` regime and the `flip` draw are stated against: FSLA's carry kernel window
#: (`fsla.ops.carry.WINDOW`; fsla's tests hold the two equal). A cell's input does not change with it, only what the
#: two claims mean.
WINDOW = 512
#: THE ONE TOKEN a ``deposit`` draw makes live. Not the window's first: a token index a kernel's segment layout has to
#: place correctly is what makes the cell a test of the layout rather than of its origin.
DEPOSIT_TOKEN = 5


def check(cell) -> None:
    """A carry cell's own rules (`form.cell` calls it): a per-level draw states one write entry and one read tuple a
    level (one tuple, or one a read head of its group), a corner or degenerate draw its topology alone; a cohort draw
    carries its run; every draw but a degenerate one states its regime."""
    from .regimes import REGIME_AXES

    r = cell.routing
    if r is None or not r.topology:
        raise ValueError("a carry cell states its routing's topology")
    for field, allowed in (("draw", DRAWS), ("backing", BACKINGS), ("state", STATES), ("tier", TIERS)):
        if getattr(cell, field) not in allowed:
            raise ValueError(f"{field} {getattr(cell, field)!r} is not one of {allowed}")
    if cell.draw in ("iid", "cohort"):
        if len(r.write) != r.D or len(r.read) not in (1, cell.group) or any(len(head) != r.D for head in r.read):
            raise ValueError(f"a {cell.draw} draw states one write entry a level and one read tuple (or one a read "
                             f"head of its group of {cell.group}) of one entry a level; got {r}")
    elif r.write or r.read:
        raise ValueError(f"a {cell.draw} draw is its own word: it states its topology alone, not entries")
    if cell.group > 1 and cell.draw not in ("iid", "cohort"):
        raise ValueError(f"a grouped cell is a per-level draw; got group {cell.group} on a {cell.draw} draw")
    if (cell.draw == "cohort") != (cell.cohort is not None):
        raise ValueError("a cohort draw carries a cohort and nothing else does")
    if (cell.draw in DEGENERATE) != (cell.regime is None):
        raise ValueError(f"a {cell.draw} draw {'declares no' if cell.draw in DEGENERATE else 'states its'} regime")
    for axis, value in cell.regime or ():
        if value not in REGIME_AXES[axis]:
            raise ValueError(f"{value!r} is not a value of the {axis} axis {REGIME_AXES[axis]}")


def declared_sparsity(cell) -> tuple[tuple[bool, bool], ...]:
    """Per level, ``(read sparse, write sparse)``: the support the routing DECLARES, which a kernel may skip on -- an
    entry that makes exact zeros (`form.sparse`), a tied read level declaring what its write side does. A corner or
    degenerate draw declares nothing. A declaration may understate what the draw carries and never overstates it."""
    from .form import sparse

    r = cell.routing
    if not r.write:
        return ((False, False),) * r.D
    return tuple((any(sparse(r.write[level] if head[level] == "tied" else head[level]) for head in r.read),
                  sparse(r.write[level])) for level in range(r.D))


# ------------------------------------------------------------------ the draw

def simplex(shape, k_tok, gen, device, live=None):
    """A row-normalized draw with exactly ``k_tok`` nonzeros per row, or dense.

    ``live`` truncates a row to its FIRST ``live`` digits -- STRUCTURED support, as against ``k_tok``'s unstructured
    one. Unstructured sparsity at long ``L`` still reaches every page, so only a truncation can produce a page the
    routing never touches (the idle-resident cell). It is applied after both draws so a cell's RNG stream does not depend
    on it.
    """
    import torch

    x = torch.rand(shape, device=device, dtype=torch.float64, generator=gen)
    if k_tok is not None and k_tok < shape[-1]:
        keep = torch.zeros(shape, device=device, dtype=torch.float64)
        idx = torch.argsort(torch.rand(shape, device=device, generator=gen), dim=-1)[..., :k_tok]
        keep.scatter_(-1, idx, 1.0)
        x = x * keep
    if live is not None and live < shape[-1]:
        x[..., live:] = 0.0
    return x / x.sum(-1, keepdim=True)


def clustered(shape, k_tok, cohort, gen, device):
    """A simplex draw whose support is ONE digit set per run of ``cohort`` consecutive tokens -- the structure a
    whole-window skip exists to exploit, and the one that leaves an owner a handful of live tokens a window. The set is
    a window of ``k_tok`` consecutive slots of a random relabeling of the level: a cohort's digits carry no label
    adjacency, as a learned router's do not (adjacent labels rewarded contiguous ownership the real draw will not)."""
    import torch

    B, T, H, width = shape
    x = torch.rand(shape, device=device, dtype=torch.float64, generator=gen)
    starts = torch.randint(0, width, (B, T // cohort, 1, 1), device=device, generator=gen)
    labels = torch.randperm(width, device=device, generator=gen)
    idx = labels[(starts.expand(B, T // cohort, cohort, 1).reshape(B, T, 1, 1)
                  + torch.arange(k_tok, device=device).view(1, 1, 1, k_tok)) % width]
    keep = torch.zeros(shape, device=device, dtype=torch.float64)
    keep.scatter_(-1, idx.expand(B, T, H, k_tok), 1.0)
    x = x * keep
    return x / x.sum(-1, keepdim=True)


@dataclass(frozen=True, slots=True)
class RealizedCell:
    """A drawn cell: the bf16 operands a kernel gets, and their fp64 doubles.

    A reference reads the ROUNDED operands, never the raw fp64 draws: comparing an fp64 oracle of the fp64 draws against
    a kernel fed their bf16 images would measure the cast, which is not what is under test.
    """

    #: the drawn cell (`form.Cell`, kind carry)
    cell: object
    read: tuple
    write: tuple
    gain: object
    v: object
    #: ``[BH, N, DV + 1]`` fp32, CANONICAL leaf order (level 0 the most significant digit): the entry state a
    #: ``carried`` cell binds (value columns ``0.1 * N(0, 1)``, the mass column their magnitudes), or None. Drawn after
    #: every operand, so binding one moves no other tensor of the cell.
    entry: object = None

    def doubles(self):
        return (tuple(x.double() for x in self.read), tuple(x.double() for x in self.write),
                self.gain.double(), self.v.double())


def _degenerate(cell, shape, gen, device):
    """The two draws with NO support to sample: nothing live, and exactly one deposit.

    ``dead`` puts zero amplitude on every digit of every level on both sides, so no token reads and no token writes --
    the recurrence's identity, whose whole content is that the state comes out as it went in. ``deposit`` is the
    single-tile point of the fold: one token, one-hot on digit zero of every write level, so exactly one leaf receives
    exactly one deposit, and the read side is dead so the readout is the zero it would be against an entry state a
    single window never reads.
    """
    import torch

    B, T, H = shape
    widths = cell.routing.topology
    zeros = tuple(torch.zeros(B, T, H, w, device=device, dtype=torch.float64) for w in widths)
    write = zeros
    if cell.draw == "deposit":
        write = tuple(torch.zeros(B, T, H, w, device=device, dtype=torch.float64) for w in widths)
        for level in write:
            level[:, DEPOSIT_TOKEN, :, 0] = 1.0
    gain = torch.rand(B, T, H, device=device, dtype=torch.float64, generator=gen) + 0.5
    v = torch.randn(B, T, H, cell.dv, device=device, dtype=torch.float64, generator=gen)
    return RealizedCell(cell=cell, read=tuple(x.to(torch.bfloat16) for x in zeros),
                        write=tuple(x.to(torch.bfloat16) for x in write), gain=gain.to(torch.bfloat16),
                        v=v.to(torch.bfloat16))


def _masked(shape, mask, gen, device):
    """A simplex draw confined to the digits ``mask`` marks (a ``[..., width]`` 0/1 tensor broadcast over ``shape``)."""
    import torch

    x = torch.rand(shape, device=device, dtype=torch.float64, generator=gen) * mask
    return x / x.sum(-1, keepdim=True)


def _one_hot(shape, gen, device):
    import torch

    digit = torch.randint(0, shape[-1], shape[:-1] + (1,), device=device, generator=gen)
    return torch.zeros(shape, device=device, dtype=torch.float64).scatter_(-1, digit, 1.0)


def _corner(cell, shape, gen, device):
    """``(read, write)`` for a CORNER draw -- a named region of the distribution a random draw does not reach.

    ``anti``: level zero reads its first half of digits and writes
    its second half, so no token reads a leaf it writes and every read leaf is cold. ``cold``: level zero's write side is
    confined to its leading ``support`` fraction under dense reads. ``readonly``: every token writes leaf zero and nothing
    else. ``concentrated``: every row carries 0.9 of its mass on one digit. ``onehot``: one live digit per row on both
    sides. ``flip``: level zero's write support is its first half of digits in even windows (`WINDOW`) and its second
    half in odd ones, so which window last wrote a leaf changes exactly at a window line.
    """
    import torch

    def dense(width):
        return simplex(shape + (width,), None, gen, device)

    def half(width, second):
        mask = torch.zeros(width, device=device, dtype=torch.float64)
        mask[width // 2:] = float(second)
        mask[:width // 2] = float(not second)
        return mask

    widths = cell.routing.topology
    if cell.draw == "onehot":
        return (tuple(_one_hot(shape + (w,), gen, device) for w in widths),
                tuple(_one_hot(shape + (w,), gen, device) for w in widths))
    if cell.draw == "concentrated":
        def peaked(width):
            x = torch.rand(shape + (width,), device=device, dtype=torch.float64, generator=gen)
            return 0.1 * x / x.sum(-1, keepdim=True) + 0.9 * _one_hot(shape + (width,), gen, device)
        return tuple(peaked(w) for w in widths), tuple(peaked(w) for w in widths)
    read = [dense(w) for w in widths]
    write = [dense(w) for w in widths]
    w0 = widths[0]
    if cell.draw == "anti":
        read[0] = _masked(shape + (w0,), half(w0, second=False), gen, device)
        write[0] = _masked(shape + (w0,), half(w0, second=True), gen, device)
    elif cell.draw == "cold":
        mask = torch.zeros(w0, device=device, dtype=torch.float64)
        mask[:max(1, int(w0 * cell.support))] = 1.0
        write[0] = _masked(shape + (w0,), mask, gen, device)
    elif cell.draw == "readonly":
        write = [torch.zeros(shape + (w,), device=device, dtype=torch.float64) for w in widths]
        for level in write:
            level[..., 0] = 1.0
    elif cell.draw == "flip":
        odd = (torch.arange(shape[1], device=device) // WINDOW) % 2 == 1
        mask = torch.where(odd.view(1, -1, 1, 1), half(w0, second=True), half(w0, second=False))
        write[0] = _masked(shape + (w0,), mask, gen, device)
    return tuple(read), tuple(write)


def realize(cell, B: int = 1, H: int | None = None, device: str = "cuda") -> RealizedCell:
    """Draw ``cell`` at ``H`` write heads (the cell's ``H`` unless given): ``[B, L, H, B_l]`` per level, ``[B, L, H]``
    gain, ``[B, L, H, DV]`` v, and a carried cell's entry -- the read levels at ``H group`` query heads for a grouped
    cell, each write head's read by its ``group`` consecutive.

    A PER-LEVEL DRAW is its routing's entries: each untied read level at the cell's read heads, then each write level,
    in level order -- `dense` a dense simplex row, `k<n>` exactly n nonzero digits a token (a cohort draw's runs), the
    `support` truncation over both -- and a `tied` read level is its write level, the same tensor. The alternation
    (Blake, 2026-08-19) is the routing whose write side is sparse at the even levels and read side at the odd ones.
    """
    import torch

    from .form import entry

    H = cell.H if H is None else H
    gen = torch.Generator(device=device).manual_seed(cell.seed)
    shape = (B, cell.tokens, H)
    widths = cell.routing.topology
    live = tuple(max(1, int(w * cell.support)) for w in widths)

    def draw(width, word, lv, at=shape):
        kt = entry(word)[1]
        if kt is None or kt >= width:
            return simplex(at + (width,), None, gen, device, live=lv)
        if cell.draw == "cohort":
            return clustered(at + (width,), kt, cell.cohort, gen, device)
        return simplex(at + (width,), kt, gen, device, live=lv)

    if cell.draw in DEGENERATE:
        drawn = _degenerate(cell, shape, gen, device)
    else:
        if cell.draw in ("iid", "cohort"):
            #: every read head of the group reads by one tuple (`check`)
            (reads,) = set(cell.routing.read)
            #: each level cast as it is drawn, one fp64 level alive at a time (the stream's order unchanged)
            untied = {level: draw(w, reads[level], live[level], (B, cell.tokens, H * cell.group)).to(torch.bfloat16)
                      for level, w in enumerate(widths) if reads[level] != "tied"}
            write = tuple(draw(w, cell.routing.write[level], live[level]).to(torch.bfloat16)
                          for level, w in enumerate(widths))
            read = tuple(untied.get(level, write[level]) for level in range(len(widths)))
        else:
            read, write = _corner(cell, shape, gen, device)
            read, write = tuple(x.to(torch.bfloat16) for x in read), tuple(x.to(torch.bfloat16) for x in write)
        gain = torch.rand(*shape, device=device, dtype=torch.float64, generator=gen) + 0.5
        v = torch.randn(*shape, cell.dv, device=device, dtype=torch.float64, generator=gen)
        drawn = RealizedCell(cell=cell, read=read, write=write, gain=gain.to(torch.bfloat16), v=v.to(torch.bfloat16))
    if cell.state == "carried":
        entry_state = 0.1 * torch.randn(B * H, cell.routing.N, cell.dv + 1, device=device, dtype=torch.float64,
                                        generator=gen)
        entry_state[..., cell.dv] = entry_state[..., cell.dv].abs()
        drawn = dataclasses.replace(drawn, entry=entry_state.to(torch.float32))
    if cell.regime is not None:
        from .regimes import prove

        prove(drawn)
    return drawn


__all__ = ["BACKINGS", "CORNERS", "DEGENERATE", "DEPOSIT_TOKEN", "DRAWS", "STATES", "TIERS", "WINDOW", "RealizedCell",
           "check", "clustered", "declared_sparsity", "realize", "simplex"]
