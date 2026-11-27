"""The central registry: every file loads into the one form, every name is computed from its cell's fields and no record
writes one, a record holds its input and nothing a package runs it with, every carry cell realizes on the device and
sits in its declared regime, the regime checks have teeth, and the frozen corners stay. Needs torch and a CUDA device --
the gate runs in a venv with both, and a missing device fails rather than skips; the device tests hold the GPU lock
shared, with the machine's lock settings but `host.nice` off, so this process's priority is left for the lock tests to
observe.
`python -m unittest tests.test_central_cells`"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import torch

from fsla_devtools import config
from fsla_devtools.cells import FILES, build, carry, cell, central, form, layer, producer, qkv
from fsla_devtools.cells.regimes import REGIME_AXES, View, check
from fsla_devtools.locks.gpu import gpu_lock

#: THE CORNERS, frozen: a named region of the distribution, once a cell, never leaves the registry.
CORNERS = ("carry-tied-k4-256x256-T1024", "carry-concentrated-256x256-T1024", "carry-flip-256x256-T1536",
           "carry-onehot-16x16-T1024", "carry-anti-64x64-T1024-carried", "carry-cold-64x64-T1024-sup0.125-carried",
           "carry-readonly-64x64-T1024-carried")
T, WIDTH = 64, 8
WINDOW = carry.WINDOW


def _simplex(p=1.0, seed=0, shape=(1, T, 1, WIDTH)):
    gen = torch.Generator().manual_seed(seed)
    x = torch.rand(shape, dtype=torch.float64, generator=gen)
    if p < 1.0:
        x = x * (torch.rand(shape, dtype=torch.float64, generator=gen) < p)
        x[..., 0] = x[..., 0] + (x.sum(-1) == 0)
    return x / x.sum(-1, keepdim=True)


def _one_hot(seed=0, digit=None):
    gen = torch.Generator().manual_seed(seed)
    idx = torch.randint(0, WIDTH, (1, T, 1, 1), generator=gen) if digit is None else torch.full((1, T, 1, 1), digit)
    return torch.zeros(1, T, 1, WIDTH, dtype=torch.float64).scatter_(-1, idx, 1.0)


def _half(second, seed):
    x = _simplex(seed=seed)
    x[..., WIDTH // 2:] *= float(second)
    x[..., :WIDTH // 2] *= float(not second)
    return x / x.sum(-1, keepdim=True)


def _view(read, write, tokens=T, entry=None):
    return View(SimpleNamespace(tokens=tokens), (read,), (write,), entry)


DENSE, OTHER = _simplex(seed=1), _simplex(seed=2)
SPARSE = _simplex(0.4, seed=3)
CONSTANT = _simplex(seed=4)[:, :1].expand(1, T, 1, WIDTH)
ENTRY = torch.ones(1, WIDTH, 2)

#: For every (axis, value): a view that demonstrates it, and one that does not.
CASES = {
    ("density", "dense"): (_view(DENSE, OTHER), _view(SPARSE, OTHER)),
    ("density", "sparse"): (_view(SPARSE, OTHER), _view(DENSE, OTHER)),
    ("coherence", "iid"): (_view(DENSE, OTHER), _view(CONSTANT, OTHER)),
    ("coherence", "coherent"): (_view(CONSTANT, OTHER), _view(DENSE, OTHER)),
    ("rw_correlation", "tied"): (_view(DENSE, DENSE), _view(DENSE, OTHER)),
    ("rw_correlation", "independent"): (_view(DENSE, OTHER), _view(DENSE, DENSE)),
    ("rw_correlation", "anti"): (_view(_half(False, 5), _half(True, 6)), _view(DENSE, OTHER)),
    ("mass", "spread"): (_view(DENSE, OTHER), _view(_one_hot(1), _one_hot(2))),
    ("mass", "concentrated"): (_view(_one_hot(1), _one_hot(2)), _view(DENSE, OTHER)),
    ("mass", "cold_read"): (_view(DENSE, _one_hot(digit=0), entry=ENTRY), _view(DENSE, OTHER, entry=ENTRY)),
    ("tail", "divisible"): (_view(DENSE, OTHER, tokens=2 * WINDOW), _view(DENSE, OTHER, tokens=WINDOW + 1)),
    ("tail", "ragged"): (_view(DENSE, OTHER, tokens=WINDOW + 1), _view(DENSE, OTHER, tokens=2 * WINDOW)),
    ("support", "singleton"): (_view(DENSE, _one_hot(2)), _view(DENSE, OTHER)),
    ("support", "partial"): (_view(SPARSE, OTHER), _view(DENSE, OTHER)),
    ("support", "full"): (_view(DENSE, OTHER), _view(SPARSE, OTHER)),
}

DENSE_16 = {"topology": [16, 16], "write": ["dense", "dense"], "read": [["dense", "dense"]]}
RECORD = {"kind": "carry", "seed": 0, "routing": DENSE_16, "tokens": 16, "tier": "oracle", "regime": None}
REGIME = {axis: values[0] for axis, values in REGIME_AXES.items()}


class RegimeChecks(unittest.TestCase):
    def test_every_value_of_every_axis_has_a_case_and_nothing_else_does(self):
        self.assertEqual(set(CASES), {(axis, value) for axis, values in REGIME_AXES.items() for value in values})
        with self.assertRaises(KeyError):
            check("temperature", "hot", CASES[("density", "dense")][0])

    def test_a_check_accepts_its_regime_and_refuses_another(self):
        for (axis, value), (inside, outside) in sorted(CASES.items()):
            with self.subTest(f"{axis}={value}"):
                self.assertIsNone(check(axis, value, inside))
                self.assertIsNotNone(check(axis, value, outside), "the check accepted a view outside it: it has no teeth")

    def test_a_cold_read_without_an_entry_state_is_refused(self):
        self.assertIsNotNone(check("mass", "cold_read", _view(DENSE, _one_hot(digit=0))))


class Records(unittest.TestCase):
    def test_a_record_states_every_axis_or_none_at_all(self):
        with self.assertRaisesRegex(ValueError, "states its regime"):
            form.cell(None, **RECORD)
        with self.assertRaisesRegex(ValueError, "not every axis"):
            form.cell(None, **dict(RECORD, regime={"density": "dense"}))
        with self.assertRaisesRegex(ValueError, "declares no regime"):
            form.cell(None, **dict(RECORD, routing={"topology": [16, 16]}, draw="dead", regime=REGIME))

    def test_a_cell_names_no_way_of_running_it(self):
        with self.assertRaisesRegex(ValueError, "undeclared field"):
            form.cell(None, **dict(RECORD, draw="dead", warps_per_cta=8))
        with self.assertRaisesRegex(ValueError, "undeclared field"):
            form.cell(None, kind="qkv", seed=0, tokens=8, backend="flash")

    def test_a_kind_holds_every_field_it_does_not_read_at_its_default(self):
        with self.assertRaisesRegex(ValueError, "reads none of"):
            form.cell(None, kind="qkv", seed=0, tokens=8, gain=2.0)
        with self.assertRaisesRegex(ValueError, "reads none of"):
            form.cell(None, **dict(RECORD, regime=REGIME, decode_steps=4))

    def test_a_corner_is_its_word_and_a_per_level_draw_its_entries(self):
        with self.assertRaisesRegex(ValueError, "states its topology alone"):
            form.cell(None, **dict(RECORD, draw="anti", regime=REGIME))
        with self.assertRaisesRegex(ValueError, "one write entry a level"):
            form.cell(None, **dict(RECORD, routing={"topology": [16, 16], "write": ["k4"], "read": [["dense"]]},
                                   regime=REGIME))
        with self.assertRaisesRegex(ValueError, "routing takes"):
            form.cell(None, **dict(RECORD, routing={"topology": [16], "write": ["softmax"], "read": [["dense"]]},
                                   regime=REGIME))

    def test_the_declared_sparsity_is_the_routings_entries(self):
        self.assertEqual(carry.declared_sparsity(cell("carry-alt-k4-16x16x16-T256")),
                         ((False, True), (True, False), (False, True)))
        #: a tied read level declares what its write side does: both sides of a tied k4 level are sparse
        self.assertEqual(carry.declared_sparsity(cell("carry-tied-k4-256x256-T1024")), ((True, True), (True, True)))
        self.assertEqual(carry.declared_sparsity(cell("carry-anti-64x64-T1024-carried")), ((False, False),) * 2)

    def test_every_file_loads_into_one_registry_of_the_one_form(self):
        cells = [build(record) for record in central().cells.values()]
        self.assertEqual({type(c) for c in cells}, {form.Cell})
        self.assertEqual({c.kind for c in cells}, set(form.KINDS))
        self.assertEqual([path.name for path in FILES],
                         ["bases.json", "carry.json", "layer.json", "producer.json", "qkv.json"])

    def test_the_corners_stay_and_the_registry_covers_the_whole_box(self):
        cells = [c for c in map(build, central().cells.values()) if c.kind == "carry"]
        names = {c.name for c in cells}
        self.assertLessEqual(set(CORNERS), names, "a frozen corner left the registry")
        covered = {pair for c in cells if c.regime for pair in c.regime}
        missing = {(axis, value) for axis, values in REGIME_AXES.items() for value in values} - covered
        self.assertFalse(missing, f"no registry cell demonstrates {sorted(missing)}")


class Names(unittest.TestCase):
    """THE NAME IS COMPUTED: `form.name` of the cell's own fields, never a hand's."""

    def test_no_central_record_writes_a_name_and_each_is_named_by_its_fields(self):
        for path in FILES:
            written = [r for r in json.loads(path.read_text()).get("cells", []) if "name" in r]
            self.assertFalse(written, f"{path.name} writes a name by hand")
        for name, record in central().cells.items():
            self.assertEqual(form.name(build(record)), name)

    def test_the_grammar_spells_each_field_off_its_standard(self):
        self.assertEqual(cell("carry-alt-k4-256x256-T1024-q4").group, 4)
        self.assertEqual(cell("carry-cohort-k16-c64-256x256-T1024").cohort, 64)
        self.assertEqual(cell("carry-dense-64x64-T256-sup0.25-carried").support, 0.25)
        self.assertEqual(cell("carry-alt-k4-256x256-T65536-h4").H, 4)
        decode = cell("layer-alt-entmax1.5-16x16-T128-h2-dec8-fp32")
        self.assertEqual((decode.H, decode.decode_steps, decode.dtype, decode.gain), (2, 8, "fp32", 1.0))
        self.assertEqual(cell("layer-dense-softmax-256-T128-h2-dec8-fp32").routing.write, ("softmax",))
        self.assertEqual(cell("layer-alt-entmax1.5-8x8x8x8-T512-g8").gain, 8.0)
        self.assertEqual(cell("producer-w16-s30-masked-a2").routing.write, ("entmax2",))
        self.assertEqual(cell("qkv-L65536-dv64-h4").H, 4)

    def test_a_field_the_grammar_cannot_spell_and_a_written_name_that_is_not_the_fields_are_refused(self):
        with self.assertRaisesRegex(ValueError, "no qkv name spells"):
            form.cell(None, kind="qkv", seed=0, tokens=8, causal=False)
        with self.assertRaisesRegex(ValueError, "no name spells the wiring"):
            #: the union wiring, level zero sparse on both sides, has no name (the owner removed it, 2026-11-24)
            form.cell(None, **dict(RECORD, routing={"topology": [16, 16], "write": ["k4", "dense"],
                                                    "read": [["k4", "dense"]]}, regime=REGIME))
        with self.assertRaisesRegex(ValueError, "name is its fields"):
            form.cell("flagship-dense", kind="qkv", seed=0, tokens=8)


class Realized(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = tempfile.TemporaryDirectory()
        host = {name: config.machine(f"host.{name}") for name in config.MACHINE["host"]}
        (Path(cls.config.name) / "host.json").write_text(json.dumps(dict(host, nice=False)))
        cls.pointer = os.environ.get(config.POINTER)
        os.environ[config.POINTER] = cls.config.name
        config.reload()

    @classmethod
    def tearDownClass(cls):
        os.environ.pop(config.POINTER) if cls.pointer is None else os.environ.__setitem__(config.POINTER, cls.pointer)
        config.reload()
        cls.config.cleanup()

    def test_every_carry_cell_realizes_in_its_regime_and_reproduces_from_its_name(self):
        with gpu_lock(mode="shared"):
            for c in map(build, central().cells.values()):
                if c.kind != "carry":
                    continue
                with self.subTest(c.name):
                    drawn = carry.realize(c)
                    self.assertEqual([tuple(x.shape) for x in drawn.read],
                                     [(1, c.tokens, c.H * c.group, w) for w in c.routing.topology])
                    self.assertEqual([tuple(x.shape) for x in drawn.write],
                                     [(1, c.tokens, c.H, w) for w in c.routing.topology])
                    self.assertEqual(drawn.entry is not None, c.state == "carried")
                    del drawn
                    torch.cuda.empty_cache()
            cold = cell("carry-cold-64x64-T1024-sup0.125-carried")
            first, again = carry.realize(cold), carry.realize(cold)
            self.assertTrue(all(torch.equal(a, b) for a, b in zip(first.write, again.write, strict=True)))
            self.assertTrue(torch.equal(first.entry, again.entry))

    def test_a_layer_cell_and_a_qkv_cell_realize_their_shapes(self):
        with gpu_lock(mode="shared"):
            drawn = layer.realize(cell("layer-alt-entmax1.5-16x16-T128-h2-dec8-fp32"))
            self.assertEqual((tuple(drawn.x.shape), tuple(drawn.v.shape)), ((2, 136, 128), (2, 136, 2, 64)))
            self.assertEqual(tuple(drawn.prefill[1].shape), (2, 128, 2, 64))
            q, k, v = qkv.realize(cell("qkv-L256-dv64"))
            self.assertEqual({tuple(t.shape) for t in (q, k, v)}, {(1, 1, 256, 64)})
            self.assertFalse(torch.equal(q, k))

    def test_a_producer_cell_draws_at_its_scale_and_masks_raggedly(self):
        """The SCALE is the cell's reason to exist -- it is what moves the support boundary two implementations
        disagree about -- and the mask is RAGGED, because a uniform mask is a narrower cell and not a masked one."""
        near_dense, mask = producer.realize(cell("producer-w256-s0.5-dense-a1.5"))
        far, ragged = producer.realize(cell("producer-w256-s30-masked-a1.5"))
        self.assertEqual((tuple(near_dense.shape), near_dense.dtype), ((8, 256), torch.float64))
        self.assertIsNone(mask)
        self.assertGreater(far.std().item(), 10 * near_dense.std().item())
        live = ragged.sum(dim=-1)
        self.assertEqual(len(set(live.tolist())), 8)  #: a different live width per row

    def test_a_producer_cell_refuses_an_alpha_no_shipped_producer_solves(self):
        with self.assertRaisesRegex(ValueError, "shipped producer"):
            form.cell(None, kind="producer", seed=0, tokens=8, routing={"topology": [8], "write": ["entmax1.7"]})


if __name__ == "__main__":
    unittest.main()
