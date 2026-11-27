"""THE TIMING SESSION PROGRAM: one interleaved session over timing entries, started by a Bazel run target and stored.

    bazel run //measure:session_entmax [-- --scratch DIR] [--no-store] [--store-root DIR]
    bazel run //measure:null_entmax          # its null gate
    bazel run //measure:memory               # a memory pass
    python -m fsla_devtools.timing.session --runfiles DIR --manifest PATH [...]   # what the target's launcher runs

`bazel/measure.bzl`'s `interleaved_timing` builds everything a session needs -- each entry's BUNDLE (its code, its
library, its build's facts, its stamped commit), each cell's description, and the session's manifest -- and its launcher
starts this program on them. Nothing outlives the run: the program starts one entry worker per bundle
(`fsla_devtools.timing.entry_worker`, on the bundle's interpreter, with the bundle's runfiles as its whole import path),
holds the machine's locks for the session, and stops every worker before it stores.

In order, it: takes the GPU lock exclusive, then the host budget whole (no build runs during a session, and nothing lowers
its priority), then the machine's clock lock; refuses a host that declares no clock lock unless the session's claim says
`unlocked`, and a locked host whose session names no clock reader; reads the machine's devices; sets every entry up on
every cell it takes BEHIND A BARRIER, each worker reporting the digest of the code it draws cells with and the file each
bound module was loaded from -- an entry drawing with other code than its cell's description names, or loading a bound
module from outside its bundle, is that member's failure; refuses two stopwatches; FAILS when no member set up, naming the
first refusal; reads the clock and proves it on the lock; warms each member past `WARMUP_FLOOR` calls; then times
rounds x reps, every rep in a fresh random order SEEDED FROM THE RUN'S ID (the seed is stored), each call preceded by the
entry's untimed reset; reads the clock again. It writes `session.json` into its scratch directory and stores it as ONE
sample at the session's own label (the manifest's `label`, `@fsla//measure:session_entmax`): the semantics are what
ran -- every entry's description, its bundle's content digest and environment, the cells, rounds, reps and warm-up,
whether the clock was locked, and the machine's devices -- and the provenance is each bundle's stamped commit. Exit 0
is stored. A refusal, a clock off the lock, two stopwatches, a worker lost or nothing set up exits non-zero and stores
nothing; samples already taken stay in the scratch directory, named.

THE NULL GATE (`interleaved_timing(null_gate = True)`, manifest `mode` `null`, stored at its own label) is the same
session with every member set up TWICE, each copy in a worker of its own (`<owner>#a`, `<owner>#b`): per entry and
cell, the per-rep ratios of the two copies, a over b, and `trusted` where their interquartile range holds one, so a
comparison across workers is read beside what the gate found (`fsla_devtools.results.verdict`). THE MEMORY PASS
(`measure_memory`, `mode` `memory`, stored at its own label) takes each entry on each cell alone under the GPU lock
and the host budget: the caching allocator's peak over its calls and what it holds outside it, `memory.json`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..config import machine
from ..locks import clock, host
from ..locks.gpu import gpu_lock
from ..results.store import run_id
from .pool import EntryWorker, Pool

#: launches of each entry discarded before any is recorded: the first build schedules and warm caches
WARMUP_FLOOR = 10
#: the claim that admits a host with no clock lock
UNLOCKED = "unlocked"


class Refused(RuntimeError):
    """A session that cannot be measured or stored: the run fails, and nothing is stored."""


@dataclass
class Member:
    """One entry on one cell in a session. `record` is what the session keeps of it (`id`, `owner`, `cell`, `executor`,
    then `status` and its `error`, `built`, `instrument`, `loaded`); `setup` the worker's set-up request; `draw` the draw
    digest its cell's description states; `bundle`, when given, the directory every module the set-up names in `binds`
    must be loaded from."""

    record: dict
    worker: EntryWorker
    setup: dict
    draw: str
    bundle: str | None = None
    ident: str = ""


def interleave(members: list[Member], *, rounds: int, reps: int, warmup: int, seed: int, env: dict | None = None,
               portable: Callable[[str], str] = str) -> tuple[list[dict], list[str]]:
    """Set every member up behind the barrier, warm them, and time `rounds` x `reps`, each rep in a fresh order drawn from
    `seed`; returns the samples in the order taken and the stopwatches the live members time with. A member that cannot
    set up or fails a call is recorded and dropped; two stopwatches, or a worker that dies, raise."""
    env = env or {}
    set_up: list[Member] = []
    try:
        for member in members:
            reply = member.worker.ask({"op": "setup", "id": member.ident, "env": env, **member.setup}, member.record["id"])
            if "failed" in reply:
                member.record.update(status="failed", error=portable(reply["failed"]))
                continue
            set_up.append(member)
            ready = reply["ready"]
            outside = {name: path for name, path in ready.get("loaded", {}).items()
                       if member.bundle is not None and not path.startswith(member.bundle.rstrip("/") + "/")}
            if ready["draw"] != member.draw:
                member.record.update(status="failed", error="the entry's worker draws cells with other code than its "
                                                            "cell's description names")
            elif outside:
                member.record.update(status="failed", error="loaded from outside the entry's bundle: " + ", ".join(
                    f"{name} from {portable(path)}" for name, path in sorted(outside.items())))
            else:
                member.record.update(status="ok", built=ready["built"], instrument=ready["instrument"])
                if member.bundle is not None:
                    member.record["loaded"] = {name: os.path.relpath(path, member.bundle)
                                               for name, path in sorted(ready.get("loaded", {}).items())}
        #: THE BARRIER: every entry has set up before any timed call
        live = [m for m in set_up if m.record["status"] == "ok"]
        instruments = sorted({m.record["instrument"] for m in live})
        if len(instruments) > 1:
            raise Refused(f"one session, one stopwatch: its entries time with {instruments}")

        def call(member: Member, what: str) -> float | None:
            """One call, or None where the ENTRY failed it: that entry's domain failure, recorded and dropped from the
            session while the rest are timed (a worker that DIES is the session's)."""
            reply = member.worker.ask({"op": "call", "id": member.ident, "env": env}, what)
            if "failed" in reply:
                member.record.update(status="failed", error=portable(reply["failed"]))
                live.remove(member)
                return None
            return reply["ms"]

        for _ in range(warmup):
            for member in list(live):
                call(member, "warmup")
        rng, samples = random.Random(seed), []
        for rnd in range(rounds):
            for rep in range(reps):
                for position, member in enumerate(rng.sample(live, len(live))):
                    ms = call(member, member.record["id"])
                    if ms is not None:
                        samples.append({"member": member.record["id"], "round": rnd, "rep": rep, "position": position,
                                        "ms": ms})
    finally:
        for member in set_up:
            if member.worker.alive:
                member.worker.ask({"op": "drop", "id": member.ident}, "drop")
    return samples, instruments


def check_shape(rounds: int, reps: int, warmup: int, what: str) -> None:
    if warmup < WARMUP_FLOOR or reps % 2 == 0 or rounds < 1:
        raise ValueError(f"{what}: warmup at least {WARMUP_FLOOR}, odd reps and a round, got {warmup}, {reps}, {rounds}")


def seed_of(run: str) -> int:
    """The order's seed, from the run's identity: two runs never walk the same order, and a run's is reproducible."""
    return int(hashlib.sha256(run.encode()).hexdigest()[:16], 16)


def devices() -> list[dict]:
    """The machine's devices as `nvidia-smi` names them; none where it is absent."""
    smi = shutil.which("nvidia-smi")
    if smi is None:
        return []
    done = subprocess.run([smi, "--query-gpu=name,driver_version,compute_cap", "--format=csv,noheader"],
                          capture_output=True, text=True, timeout=120)
    if done.returncode:
        raise Refused(f"nvidia-smi could not name the machine's devices (exit {done.returncode}): {done.stderr.strip()}")
    return [dict(zip(("name", "driver", "compute_cap"), (f.strip() for f in line.split(",")), strict=True))
            for line in done.stdout.splitlines() if line.strip()]


def _store(label: str, root: str | None, semantics: dict, session_file: Path, provenance: dict, run: str,
           summary: dict) -> dict:
    """ONE sample of the session at its own label, a timing (it names its commits). The one import of the store's code."""
    from fsla_devtools.results import Store

    return Store(label, root).put(semantics, output_file=session_file, provenance=provenance, run=run, timing=True,
                                  summary=summary)


def _read(runfiles: Path, path: str) -> dict:
    return json.loads((runfiles / path).read_text())


def _worker(pool: Pool, runfiles: Path, entry: dict, instance: str = "") -> EntryWorker:
    root = str(runfiles / entry["bundle"]["root"])
    return pool.worker({"python": entry["bundle"]["python"], "cwd": root, "pythonpath": root, "instance": instance})


def _portable(runfiles: Path) -> Callable[[str], str]:
    """A message with the runfiles' and the home's paths taken out: a record names no machine path."""
    home = str(Path.home())
    roots = sorted({str(runfiles) + "/", os.path.realpath(runfiles) + "/"}, key=len, reverse=True)

    def portable(text: str) -> str:
        for root in roots:
            text = text.replace(root, "")
        return text.replace(home + "/", "~/")

    return portable


def _members(entries: list[dict], runfiles: Path, pool: Pool, run: str, cells: dict, copies=("",)) -> list[Member]:
    """One member an entry, a cell and a copy: a null gate's two copies of an entry are each in a worker of their own
    (`#a`, `#b`), a session's one copy in its bundle's worker."""
    members = []
    for entry in entries:
        for copy in copies:
            worker = _worker(pool, runfiles, entry, copy)
            for cell_path in entry["cells"]:
                cell = cells.setdefault(cell_path, _read(runfiles, cell_path))
                i = len(members)
                members.append(Member(
                    record={"id": f"m{i}", "owner": entry["owner"] + (f"#{copy}" if copy else ""), "cell": cell["name"],
                            "executor": entry["executor"]},
                    worker=worker, draw=cell["draw"], bundle=os.path.realpath(runfiles / entry["bundle"]["root"]),
                    ident=f"{run}:{i}",
                    setup={"executor": entry["executor"], "cell": {k: cell[k] for k in ("name", "data", "params")},
                           "params": entry["params"], "binds": entry["binds"]}))
    return members


def _quartiles(values: list[float]) -> tuple[float, float, float]:
    ordered = sorted(values)
    return ordered[int(0.25 * len(ordered))], ordered[len(ordered) // 2], ordered[int(0.75 * len(ordered))]


def null_gate(entries: list[dict], records: list[dict], samples: list[dict], rounds: int, reps: int) -> list[dict]:
    """THE NULL GATE'S FINDING, one row an entry on a cell: its two copies' per-rep ratios (a over b), and `trusted`
    where their interquartile range holds one -- a comparison across workers then carries no worker's bias; None where
    a copy could not run, with its error."""
    by = {(r["owner"], r["cell"]): r for r in records}
    ms = {(s["member"], s["round"], s["rep"]): s["ms"] for s in samples}
    rows = []
    for owner, cell in sorted({(e["owner"], r["cell"]) for e in entries for r in records
                               if r["owner"] == e["owner"] + "#a"}):
        a, b = by[(owner + "#a", cell)], by[(owner + "#b", cell)]
        if a["status"] != "ok" or b["status"] != "ok":
            rows.append({"owner": owner, "cell": cell, "trusted": None, "error": a.get("error") or b.get("error")})
            continue
        ratios = [ms[(a["id"], r, k)] / ms[(b["id"], r, k)] for r in range(rounds) for k in range(reps)
                  if (a["id"], r, k) in ms and ms.get((b["id"], r, k), 0) > 0]
        if not ratios:
            rows.append({"owner": owner, "cell": cell, "trusted": None, "error": "no rep timed both copies"})
            continue
        q1, median, q3 = _quartiles(ratios)
        rows.append({"owner": owner, "cell": cell, "trusted": q1 <= 1.0 <= q3, "ratio_median": median, "ratio_q1": q1,
                     "ratio_q3": q3})
    return rows


def _described(entry: dict, bundles: dict, cells: dict) -> dict:
    bundle = bundles[entry["owner"]]
    return {"owner": entry["owner"], "kind": entry["kind"], "executor": entry["executor"], "params": entry["params"],
            "binds": entry["binds"], "cells": [cells[c] for c in entry["cells"]],
            "bundle": {"digest": bundle["digest"], "environment": bundle["environment"]}}


def _nothing_set_up(name: str, records: list[dict]) -> Refused:
    first = next((r for r in records if r.get("error")), None)
    return Refused(f"no member of {name} set up, so nothing was measured; the first refusal, "
                   f"{first['owner']} on {first['cell']}:\n{first['error']}" if first else
                   f"{name} has no member: its entries take no cell")


def measure(manifest: dict, runfiles: Path, *, run: str, scratch: Path, read_devices=devices) -> tuple[dict, dict, dict]:
    """What the manifest declares, measured: `(output, semantics, provenance)`, the output written into `scratch`
    (`session.json`; a memory pass's `memory.json`). A session, or its null gate (`mode` `null`: every member twice, in
    two workers); a memory pass (`memory`). Raises `Refused` for what fails the run."""
    p = manifest
    if p.get("mode") == "memory":
        return memory(p, runfiles, run=run, scratch=scratch, read_devices=read_devices)
    check_shape(p["rounds"], p["reps"], p["warmup"], p["session"])
    entries = [_read(runfiles, path) for path in p["entries"]]
    reader = _read(runfiles, p["clock"]) if p.get("clock") else None
    bundles = {e["owner"]: _read(runfiles, e["bundle"]["manifest"]) for e in [*entries, *([reader] if reader else [])]}
    portable = _portable(runfiles)
    gate = p.get("mode") == "null"

    seed, pool = seed_of(run), Pool()
    try:
        with gpu_lock(mode="exclusive"), host.acquire(exclusive=True, label=f"timing session {p['session']}"), \
                clock.held() as cfg:
            if cfg is None and p["claim"] != UNLOCKED:
                raise Refused(f"this host declares no clock lock (its dev config's clock.json): a session here is "
                              f"measured only under the claim {UNLOCKED!r}, which {p['session']} does not make")
            if cfg is not None and reader is None:
                raise Refused(f"this host locks its clock and {p['session']} names no clock reader to prove it")
            facts = {"devices": read_devices()}

            def read_clock() -> float | None:
                if reader is None:
                    return None
                return _worker(pool, runfiles, reader).ask({"op": "clock", "executor": reader["executor"]},
                                                           "the clock reader")["ghz"]

            cells: dict = {}
            members = _members(entries, runfiles, pool, run, cells, copies=("a", "b") if gate else ("",))
            before = read_clock()
            if not clock.within(before, cfg):
                raise Refused(f"CLOCK: the device reads {before} GHz before the session, off the lock at {cfg['ghz']} GHz")
            samples, instruments = interleave(members, rounds=p["rounds"], reps=p["reps"], warmup=p["warmup"], seed=seed,
                                              portable=portable)
            after = read_clock()
    finally:
        pool.close()
    records = [m.record for m in members]
    session = {"session": p["session"], "run": run, "claim": p["claim"], "instrument": instruments[0] if instruments else None,
               "rounds": p["rounds"], "reps": p["reps"], "warmup": p["warmup"], "seed": seed,
               "clock": {"declared": cfg["ghz"] if cfg else None, "locked": cfg is not None, "before": before, "after": after},
               "machine": facts, "bundles": bundles, "members": records, "samples": samples}
    if gate:
        session["null"] = null_gate(entries, records, samples, p["rounds"], p["reps"])
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "session.json").write_text(json.dumps(session, sort_keys=True))
    if not any("built" in r for r in records):
        raise _nothing_set_up(p["session"], records)
    if not clock.within(after, cfg):
        raise Refused(f"CLOCK: the device reads {after} GHz after the session, off the lock at {cfg['ghz']} GHz")
    semantics = {"session": p["session"], "claim": p["claim"], "rounds": p["rounds"], "reps": p["reps"],
                 "warmup": p["warmup"], "clock": "locked" if cfg is not None else UNLOCKED, "machine": facts,
                 "entries": [_described(e, bundles, cells) for e in entries],
                 "clock_reader": _described(reader, bundles, cells) if reader else None}
    if gate:
        semantics["null_gate"] = True
    provenance = {"run": run, "checkouts": {owner: bundle["stamp"] for owner, bundle in sorted(bundles.items())}}
    return session, semantics, provenance


def memory(manifest: dict, runfiles: Path, *, run: str, scratch: Path, read_devices=devices) -> tuple[dict, dict, dict]:
    """THE MEMORY PASS: each entry on each cell alone, under the GPU lock and the host budget whole -- the caching
    allocator's peak over its `calls` (each after its reset), what stayed allocated, and the bytes it holds outside the
    allocator. A member that cannot set up is that row's failure; none that ran fails the pass."""
    p = manifest
    entries = [_read(runfiles, path) for path in p["entries"]]
    bundles = {e["owner"]: _read(runfiles, e["bundle"]["manifest"]) for e in entries}
    portable, pool, cells, rows = _portable(runfiles), Pool(), {}, []
    try:
        with gpu_lock(mode="exclusive"), host.acquire(exclusive=True, label=f"memory pass {p['session']}"):
            facts = {"devices": read_devices()}
            for entry in entries:
                worker = _worker(pool, runfiles, entry)
                for cell_path in entry["cells"]:
                    cell = cells.setdefault(cell_path, _read(runfiles, cell_path))
                    reply = worker.ask({"op": "memory", "executor": entry["executor"], "params": entry["params"],
                                        "cell": {k: cell[k] for k in ("name", "data", "params")}, "calls": p["calls"]},
                                       f"{entry['owner']} on {cell['name']}")
                    row = {"owner": entry["owner"], "cell": cell["name"], "executor": entry["executor"]}
                    row.update({"status": "failed", "error": portable(reply["failed"])} if "failed" in reply
                               else {"status": "ok", **reply["memory"]})
                    rows.append(row)
    finally:
        pool.close()
    out = {"session": p["session"], "run": run, "calls": p["calls"], "machine": facts, "bundles": bundles, "rows": rows}
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "memory.json").write_text(json.dumps(out, sort_keys=True))
    if not any(r["status"] == "ok" for r in rows):
        raise _nothing_set_up(p["session"], rows)
    semantics = {"session": p["session"], "mode": "memory", "calls": p["calls"], "machine": facts,
                 "entries": [_described(e, bundles, cells) for e in entries]}
    provenance = {"run": run, "checkouts": {owner: bundle["stamp"] for owner, bundle in sorted(bundles.items())}}
    return out, semantics, provenance


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m fsla_devtools.timing.session")
    ap.add_argument("--runfiles", required=True, help="the run target's runfiles directory")
    ap.add_argument("--manifest", required=True, help="the session's manifest, by its path in the runfiles")
    ap.add_argument("--scratch", help="where the output is written (default: the dev config's host.scratch)")
    ap.add_argument("--no-store", action="store_true", help="measure and keep the output, store nothing")
    ap.add_argument("--store-root", help="the store's records directory (default: the store's own)")
    a = ap.parse_args(argv)
    runfiles = Path(a.runfiles).absolute()
    manifest = _read(runfiles, a.manifest)
    run = run_id()
    scratch = Path(a.scratch) if a.scratch else Path(machine("host.scratch")) / "timing" / run
    name = "memory.json" if manifest.get("mode") == "memory" else "session.json"
    try:
        out, semantics, provenance = measure(manifest, runfiles, run=run, scratch=scratch)
    except RuntimeError as ex:  #: a refusal, or a worker lost (`pool.EntryWorker.ask`)
        kept = scratch / name
        print(f"SESSION FAILED: {ex}" + (f"\nits samples are kept in {kept} and stored by nothing" if kept.exists() else ""),
              file=sys.stderr)
        return 1
    if "rows" in out:
        statuses = [{k: r.get(k) for k in ("owner", "cell", "status", "error")} for r in out["rows"]]
        for r in out["rows"]:
            print(f"{r['owner']} on {r['cell']}: " + (f"peak {r['peak_allocated_bytes']} bytes" if r["status"] == "ok"
                                                      else f"failed -- {r['error'].splitlines()[-1]}"))
        summary = {"rows": statuses}
    else:
        statuses = [{k: m.get(k) for k in ("id", "owner", "cell", "status", "error")} for m in out["members"]]
        for m in statuses:
            print(f"{m['id']} {m['owner']} on {m['cell']}: {m['status']}" + (f" -- {m['error'].splitlines()[-1]}"
                                                                             if m.get("error") else ""))
        for row in out.get("null", ()):
            print(f"null gate {row['owner']} on {row['cell']}: " + ("no finding" if row["trusted"] is None else
                                                                   "trusted" if row["trusted"] else "A WORKER'S BIAS"))
        print(f"{len(out['samples'])} samples, seed {out['seed']}, clock {semantics['clock']}"
              f" ({out['clock']['before']} -> {out['clock']['after']} GHz)")
        summary = {"members": statuses, "samples": len(out["samples"]), **({"null": out["null"]} if "null" in out else {})}
    if a.no_store:
        print(f"not stored: {scratch / name}")
        return 0
    sample = _store(manifest["label"], a.store_root, semantics, scratch / name, provenance, run, summary)
    shutil.rmtree(scratch, ignore_errors=True)
    print(f"stored: {manifest['label']} sample {sample['run']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))


__all__ = ["Member", "Refused", "UNLOCKED", "WARMUP_FLOOR", "check_shape", "devices", "interleave", "measure", "memory",
           "null_gate", "run_id", "seed_of"]
