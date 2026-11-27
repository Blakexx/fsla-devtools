"""THE ONE FORM OF A CELL: every central cell, of every kind, is one record (`Cell`), and its name is computed from its
own fields by one function (`name`), never written by hand.

A cell is an input: what a measurement or a test runs on, drawn from the seed it states. Its KIND says what it feeds --

    carry      a sparse recurrence's input: routing amplitudes drawn on the simplex, a gain, values and the state the
               sequence enters with (`fsla_devtools.cells.carry`)
    layer      a sequence layer: hidden states and values (`fsla_devtools.cells.layer`) and the routing and logit gain
               the layer's producer is built with
    producer   one routing level's logits at a logit gain, with the mask and the activation they are solved at
               (`fsla_devtools.cells.producer`)
    qkv        attention's queries, keys and values (`fsla_devtools.cells.qkv`)

-- and every kind is described by the same fields (`Cell`), a field a kind does not read held at its default. A reader
refuses a cell by a FACT of the cell -- its routing's widths or depth, its value width, its decode steps, its backing,
its group -- and never by its name.

THE ROUTING is stated in FSLA's own terms (`fsla.Routing(topology, write, read)`): the level widths once (`topology`),
what the write side does on each level (`write`, one entry a level), and what each READ HEAD of a group does on each
level (`read`, one tuple a read head, or one tuple every head reads by). An entry is one word:

    dense, k<n>          a carry level drawn on the simplex dense, or with exactly n nonzero digits a token
    softmax, entmax<a>   a layer's or a producer's activation on the level
    tied                 a read entry: the level's read IS the write side's (one draw, or one solve)

so a layer and a carry cell state their routing the same way. A CORNER carry draw -- a named region a per-level draw
does not reach (`carry.CORNERS`) -- and the two DEGENERATE draws state their topology alone and are their `draw` word.

THE NAME is `name(cell)`:

    <kind>-<routing>-<widths>-T<tokens>[-q<group>][-dv<width>][-sup<support>][-<backing>][-<state>][-h<heads>]
        [-g<gain>][-dec<steps>][-<dtype>]

`routing` is the wiring read off the entries -- `dense`, `alt` (the write side sparse at the even levels, the read side
at the odd), `read` or `write` (that side sparse at level zero alone), `tied` -- and the level operator where it is not
the wiring's own word (`alt-k4`, `dense-softmax`, `alt-entmax1.5`, `tied-k4`); a cohort draw is `cohort-k<n>-c<run>`, a
corner its word. `widths` is the topology, `256x256`. A field at its kind's STANDARD (`STANDARD`) is not named; a field
the grammar does not spell (`UNNAMED`: the seed, the batch, the tier, the regime, a layer's hidden width, the note) is
not named either, and two cells whose names agree are refused when the registry loads. A producer is
`producer-w<width>-s<gain>-<dense|masked>-a<alpha>` and a QKV cell `qkv-L<tokens>-dv<width>[-h<heads>]`. A field off its
standard that the grammar has no spelling for is refused (`name` raises), so a name never hides a field.

Standard library only: a registry loads, names and checks its cells without torch.
"""
from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass

KINDS = ("carry", "layer", "producer", "qkv")
#: the dtypes a cell is drawn in, by the torch dtype each names
DTYPES = {"bf16": "bfloat16", "fp32": "float32"}
#: a level-side's entry: its operator and its parameter (`entry`)
_ENTRY = re.compile(r"^(dense|softmax|tied|k(?P<k>[1-9][0-9]*)|entmax(?P<alpha>[0-9]+(\.[0-9]+)?))$")
#: the entries each kind's routing takes
ENTRIES = {"carry": ("dense", "k", "tied"), "layer": ("softmax", "entmax", "tied"), "producer": ("entmax",)}


@dataclass(frozen=True, slots=True)
class Routing:
    """The routing as data, in `fsla.Routing`'s terms: the level widths outermost first, one write entry a level, and
    one read tuple a read head (or one every head reads by). A corner carry draw states `topology` alone."""

    topology: tuple[int, ...]
    write: tuple[str, ...] = ()
    read: tuple[tuple[str, ...], ...] = ()

    @property
    def D(self) -> int:
        return len(self.topology)

    @property
    def N(self) -> int:
        n = 1
        for width in self.topology:
            n *= width
        return n


def entry(word: str) -> tuple[str, float | int | None]:
    """``(operator, parameter)`` of one entry: ``("k", 4)``, ``("entmax", 1.5)``, ``("dense", None)``."""
    match = _ENTRY.match(word)
    if match is None:
        raise ValueError(f"{word!r} is not a routing entry (dense, k<n>, softmax, entmax<alpha>, tied)")
    if match["k"]:
        return "k", int(match["k"])
    if match["alpha"]:
        return "entmax", float(match["alpha"])
    return word, None


def sparse(word: str) -> bool:
    """Whether an entry makes exact zeros: a counted draw or an entmax solve."""
    return entry(word)[0] in ("k", "entmax")


@dataclass(frozen=True, slots=True)
class Cell:
    """One cell, of any kind. Nothing derived is stored: the name, the depth and the leaf count are computed."""

    kind: str
    seed: int
    #: T, the tokens a sequence (a producer's: the logit rows drawn)
    tokens: int
    routing: Routing | None = None
    #: the sequences a call draws
    B: int = 1
    #: the WRITE heads (FSLA's `num_heads`; attention's heads)
    H: int = 1
    #: GROUPED QUERIES: the read heads each write head's state is read by (FSLA's `group`)
    group: int = 1
    dv: int = 64
    dtype: str = "bf16"
    #: a layer's hidden width; 0 means ``H * dv``
    hidden: int = 0
    #: single-token steps a layer takes after its prefill
    decode_steps: int = 0
    #: the LOGIT GAIN: a layer's routing projection is scaled by it, a producer's logits are drawn at it
    gain: float = 1.0
    #: a carry draw's support structure: `iid` (each token its entries' draw), `cohort` (runs of `cohort` tokens share
    #: one digit window a sparse level), a corner's word, or a degenerate draw's (`carry.DRAWS`)
    draw: str = "iid"
    cohort: int | None = None
    #: the leading fraction of each carry level's digits the draw may use (for a `cold` draw: of level zero's writes)
    support: float = 1.0
    #: the state backing a carry cell binds, and whether it enters empty (`fresh`), carries a drawn state (`carried`)
    #: or binds none
    backing: str = "dense"
    state: str = "fresh"
    #: a carry draw's place in the routing distribution, ``((axis, value), ...)`` on every regime axis
    regime: tuple[tuple[str, str], ...] | None = None
    #: who a carry cell is sized for: `oracle`, `probe` or `both`
    tier: str | None = None
    #: a producer's logits under a ragged mask
    masked: bool = False
    #: an attention cell's causality
    causal: bool = True
    note: str = ""

    @property
    def name(self) -> str:
        return name(self)

    @property
    def hidden_size(self) -> int:
        return self.hidden or self.H * self.dv

    @property
    def torch_dtype(self):
        import torch

        return getattr(torch, DTYPES[self.dtype])


#: what a kind reads, beside `kind`, `seed`, `tokens` and `note`: every other field is held at its default
READS = {
    "carry": ("routing", "H", "group", "dv", "draw", "cohort", "support", "backing", "state", "regime", "tier"),
    "layer": ("routing", "B", "H", "group", "dv", "dtype", "hidden", "decode_steps", "gain"),
    "producer": ("routing", "gain", "masked"),
    "qkv": ("B", "H", "dv", "dtype", "causal"),
}
#: a field at its kind's STANDARD is not named: a field's default (`Cell`), except where a kind's standard differs -- a
#: layer's eight write heads (the pinned layer's shape), a producer's eight logit rows
STANDARD = {"carry": {}, "layer": {"H": 8}, "producer": {"tokens": 8}, "qkv": {}}
#: what a name never spells: the draw's seed, the batch, who a cell is sized for, where its draw sits, a layer's
#: hidden width, and the note
UNNAMED = ("kind", "seed", "B", "tier", "regime", "hidden", "note")


def _standard(cell: Cell, field: str):
    return STANDARD[cell.kind].get(field, _DEFAULTS.get(field))


_DEFAULTS = {f.name: f.default for f in dataclasses.fields(Cell) if f.default is not dataclasses.MISSING}


def _g(x: float) -> str:
    return f"{x:g}"


def _wiring(routing: Routing) -> str:
    """The routing word of a per-level routing: its wiring, then the level operator where it is not the wiring's own."""
    heads = set(routing.read)
    if len(heads) != 1:
        raise ValueError(f"no name spells read heads that differ ({routing.read})")
    (read,) = heads
    write = routing.write
    if all(word == "tied" for word in read):
        ops = set(write)
        if len(ops) != 1:
            raise ValueError(f"no name spells a tied routing whose levels differ ({write})")
        return f"tied-{ops.pop()}"
    if "tied" in read:
        raise ValueError(f"no name spells a routing tied on some levels ({read})")
    levels = range(routing.D)
    on_write = {level for level in levels if sparse(write[level])}
    on_read = {level for level in levels if sparse(read[level])}
    if not on_write | on_read:
        ops = set(write) | set(read)
        if len(ops) != 1:
            raise ValueError(f"no name spells a dense routing of two operators ({sorted(ops)})")
        op = ops.pop()
        return "dense" if op == "dense" else f"dense-{op}"
    if routing.D >= 2 and on_write == set(levels[::2]) and on_read == set(levels[1::2]):
        wiring = "alt"
    elif (on_write, on_read) == ({0}, set()):
        wiring = "write"
    elif (on_write, on_read) == (set(), {0}):
        wiring = "read"
    else:
        raise ValueError(f"no name spells the wiring write {write}, read {read}")
    ops = {write[level] for level in on_write} | {read[level] for level in on_read}
    if len(ops) != 1:
        raise ValueError(f"no name spells a wiring of two sparse operators ({sorted(ops)})")
    return f"{wiring}-{ops.pop()}"


def name(cell: Cell) -> str:
    """THE NAME of a cell, computed from its fields (the grammar is the module's)."""
    if cell.kind == "producer":
        (level,) = cell.routing.write
        parts = [f"w{cell.routing.topology[0]}", f"s{_g(cell.gain)}", "masked" if cell.masked else "dense",
                 f"a{_g(entry(level)[1])}"]
        spelled = {"routing", "gain", "masked"}
    elif cell.kind == "qkv":
        heads = [f"h{cell.H}"] if _standard(cell, "H") != cell.H else []
        parts = [f"L{cell.tokens}", f"dv{cell.dv}", *heads]
        spelled = {"tokens", "dv", "H"}
    else:
        parts = [_routing_word(cell), "x".join(map(str, cell.routing.topology)), f"T{cell.tokens}"]
        spelled = {"routing", "draw", "cohort", "tokens"}
        for field, spell in _SUFFIXES:
            spelled.add(field)
            if getattr(cell, field) != _standard(cell, field):
                parts.append(spell(getattr(cell, field)))
    unspelled = [f.name for f in dataclasses.fields(Cell) if f.name not in spelled | set(UNNAMED)
                 and getattr(cell, f.name) != _standard(cell, f.name)]
    if unspelled:
        raise ValueError(f"no {cell.kind} name spells {unspelled} off the standard")
    return "-".join([cell.kind, *parts])


#: a carry or layer name's suffixes, in order: each field spelled where it is off its kind's standard
_SUFFIXES = (("group", lambda v: f"q{v}"), ("dv", lambda v: f"dv{v}"), ("support", lambda v: f"sup{_g(v)}"),
             ("backing", str), ("state", str), ("H", lambda v: f"h{v}"), ("gain", lambda v: f"g{_g(v)}"),
             ("decode_steps", lambda v: f"dec{v}"), ("dtype", str))


def _routing_word(cell: Cell) -> str:
    """A carry or layer cell's routing word: the wiring of its entries, a cohort draw's run, or a corner's own word."""
    if cell.draw == "iid":
        return _wiring(cell.routing)
    if cell.draw == "cohort":
        wiring, _, op = _wiring(cell.routing).partition("-")
        if wiring != "alt":
            raise ValueError(f"no name spells a cohort draw on the {wiring} wiring")
        return f"cohort-{op}-c{cell.cohort}"
    return cell.draw


def _routing(kind: str, value) -> Routing | None:
    if value is None:
        return None
    unknown = sorted(set(value) - {"topology", "write", "read"})
    if unknown:
        raise ValueError(f"a routing states topology, write and read; got {unknown}")
    routing = Routing(topology=tuple(value["topology"]), write=tuple(value.get("write", ())),
                      read=tuple(tuple(head) for head in value.get("read", ())))
    for word in (*routing.write, *(w for head in routing.read for w in head)):
        if entry(word)[0] not in ENTRIES.get(kind, ()):
            raise ValueError(f"a {kind} routing takes {ENTRIES.get(kind, ())} entries; got {word!r}")
    if "tied" in routing.write:
        raise ValueError("`tied` is a read entry: the write side is what a tied level reads")
    return routing


def cell(cell_name: str | None = None, **fields) -> Cell:
    """THE DATA PROVIDER of every central cell (each registry file names it): the record's fields, checked against its
    kind (`READS`, and the kind's own `check`), and its name -- given by the registry, which computes it -- held to the
    one `name` computes."""
    import importlib

    unknown = sorted(set(fields) - set(_DEFAULTS) - {"kind", "seed", "tokens"})
    if unknown:
        raise ValueError(f"{cell_name}: undeclared field(s) {unknown}")
    kind = fields.get("kind")
    if kind not in KINDS:
        raise ValueError(f"{cell_name}: kind {kind!r} is not one of {KINDS}")
    from .regimes import REGIME_AXES

    regime = fields.get("regime")
    if regime is not None and set(regime) != set(REGIME_AXES):
        raise ValueError(f"{cell_name}: the regime states {sorted(regime)}, not every axis of {list(REGIME_AXES)}")
    made = Cell(**{**fields, "routing": _routing(kind, fields.get("routing")),
                   "regime": None if regime is None else tuple((axis, regime[axis]) for axis in REGIME_AXES)})
    foreign = [f.name for f in dataclasses.fields(Cell) if f.name in _DEFAULTS and f.name not in READS[kind]
               and getattr(made, f.name) != _DEFAULTS[f.name] and f.name != "note"]
    if foreign:
        raise ValueError(f"{cell_name}: a {kind} cell reads none of {foreign}")
    if made.dtype not in DTYPES:
        raise ValueError(f"{cell_name}: dtype {made.dtype!r} is not one of {sorted(DTYPES)}")
    if made.B < 1 or made.H < 1 or made.group < 1 or made.tokens < 1:
        raise ValueError(f"{cell_name}: a cell draws one or more sequences, heads, read heads and tokens")
    importlib.import_module(f"{__package__}.{kind}").check(made)
    #: every cell has a name: one the grammar cannot spell is refused here
    computed = made.name
    if cell_name is not None and cell_name != computed:
        raise ValueError(f"a cell's name is its fields' ({computed}); the record names {cell_name}")
    return made


__all__ = ["DTYPES", "ENTRIES", "KINDS", "READS", "STANDARD", "UNNAMED", "Cell", "Routing", "cell", "entry",
           "name", "sparse"]
