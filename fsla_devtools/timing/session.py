"""THE TIMING SESSION PROGRAM: one interleaved session over timing entries, started by a Bazel run target and stored.

    bazel run //measure:session_entmax [-- --scratch DIR] [--no-store] [--store-root DIR]
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

The interleaving itself (`interleave`) is also the declared build system's session (`fsla_devtools.timing.executors`).
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


def _worker(pool: Pool, runfiles: Path, entry: dict) -> EntryWorker:
    root = str(runfiles / entry["bundle"]["root"])
    return pool.worker({"python": entry["bundle"]["python"], "cwd": root, "pythonpath": root})


def measure(manifest: dict, runfiles: Path, *, run: str, scratch: Path, read_devices=devices) -> tuple[dict, dict, dict]:
    """The session the manifest declares, measured: `(session, semantics, provenance)`, `session.json` written into
    `scratch`. Raises `Refused` for what fails the run."""
    p = manifest
    check_shape(p["rounds"], p["reps"], p["warmup"], p["session"])
    entries = [_read(runfiles, path) for path in p["entries"]]
    reader = _read(runfiles, p["clock"]) if p.get("clock") else None
    bundles = {e["owner"]: _read(runfiles, e["bundle"]["manifest"]) for e in [*entries, *([reader] if reader else [])]}
    home = str(Path.home())

    roots = sorted({str(runfiles) + "/", os.path.realpath(runfiles) + "/"}, key=len, reverse=True)

    def portable(text: str) -> str:
        for root in roots:
            text = text.replace(root, "")
        return text.replace(home + "/", "~/")

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

            members, cells = [], {}
            for entry in entries:
                worker = _worker(pool, runfiles, entry)
                for cell_path in entry["cells"]:
                    cell = cells.setdefault(cell_path, _read(runfiles, cell_path))
                    i = len(members)
                    members.append(Member(
                        record={"id": f"m{i}", "owner": entry["owner"], "cell": cell["name"], "executor": entry["executor"]},
                        worker=worker, draw=cell["draw"], bundle=os.path.realpath(runfiles / entry["bundle"]["root"]),
                        ident=f"{run}:{i}",
                        setup={"executor": entry["executor"], "cell": {k: cell[k] for k in ("name", "data", "params")},
                               "params": entry["params"], "binds": entry["binds"]}))
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
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "session.json").write_text(json.dumps(session, sort_keys=True))
    if not any("built" in r for r in records):
        first = next((r for r in records if r.get("error")), None)
        raise Refused(f"no member of {p['session']} set up, so nothing was measured; the first refusal, "
                      f"{first['owner']} on {first['cell']}:\n{first['error']}" if first else
                      f"{p['session']} has no member: its entries take no cell")
    if not clock.within(after, cfg):
        raise Refused(f"CLOCK: the device reads {after} GHz after the session, off the lock at {cfg['ghz']} GHz")

    def described(entry: dict) -> dict:
        bundle = bundles[entry["owner"]]
        return {"owner": entry["owner"], "kind": entry["kind"], "executor": entry["executor"], "params": entry["params"],
                "binds": entry["binds"], "cells": [cells[c] for c in entry["cells"]],
                "bundle": {"digest": bundle["digest"], "environment": bundle["environment"]}}

    semantics = {"session": p["session"], "claim": p["claim"], "rounds": p["rounds"], "reps": p["reps"],
                 "warmup": p["warmup"], "clock": "locked" if cfg is not None else UNLOCKED, "machine": facts,
                 "entries": [described(e) for e in entries], "clock_reader": described(reader) if reader else None}
    provenance = {"run": run, "checkouts": {owner: bundle["stamp"] for owner, bundle in sorted(bundles.items())}}
    return session, semantics, provenance


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m fsla_devtools.timing.session")
    ap.add_argument("--runfiles", required=True, help="the run target's runfiles directory")
    ap.add_argument("--manifest", required=True, help="the session's manifest, by its path in the runfiles")
    ap.add_argument("--scratch", help="where session.json is written (default: the dev config's host.scratch)")
    ap.add_argument("--no-store", action="store_true", help="measure and keep session.json, store nothing")
    ap.add_argument("--store-root", help="the store's records directory (default: the store's own)")
    a = ap.parse_args(argv)
    runfiles = Path(a.runfiles).absolute()
    manifest = _read(runfiles, a.manifest)
    run = run_id()
    scratch = Path(a.scratch) if a.scratch else Path(machine("host.scratch")) / "timing" / run
    try:
        session, semantics, provenance = measure(manifest, runfiles, run=run, scratch=scratch)
    except RuntimeError as ex:  #: a refusal, or a worker lost (`pool.EntryWorker.ask`)
        kept = scratch / "session.json"
        print(f"SESSION FAILED: {ex}" + (f"\nits samples are kept in {kept} and stored by nothing" if kept.exists() else ""),
              file=sys.stderr)
        return 1
    statuses = [{k: m.get(k) for k in ("id", "owner", "cell", "status", "error")} for m in session["members"]]
    for m in statuses:
        print(f"{m['id']} {m['owner']} on {m['cell']}: {m['status']}" + (f" -- {m['error'].splitlines()[-1]}"
                                                                         if m.get("error") else ""))
    print(f"{len(session['samples'])} samples, seed {session['seed']}, clock {semantics['clock']}"
          f" ({session['clock']['before']} -> {session['clock']['after']} GHz)")
    if a.no_store:
        print(f"not stored: {scratch / 'session.json'}")
        return 0
    sample = _store(manifest["label"], a.store_root, semantics, scratch / "session.json", provenance, run,
                    {"members": statuses, "samples": len(session["samples"])})
    shutil.rmtree(scratch, ignore_errors=True)
    print(f"stored: {manifest['label']} sample {sample['run']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))


__all__ = ["Member", "Refused", "UNLOCKED", "WARMUP_FLOOR", "check_shape", "devices", "interleave", "measure", "run_id",
           "seed_of"]
