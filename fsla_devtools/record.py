"""THE RECORD OF A BUILD OUTPUT: the program a `results_store` run target starts, storing each file of its source as a
sample of the record its semantics key, unless the record already holds it.

    bazel run //measure:store_static_manifest [-- --store-root DIR]
    bazel run //measure:store_oracle_tier [-- --no-store] [--scratch DIR]
    python -m fsla_devtools.record --runfiles DIR --record PATH [...]   # what the target's launcher runs

`bazel/measure.bzl`'s `results_store` builds `<name>.record.json`: the source's label, the store target's own label
(which keys its samples), the digest of every input the source's output depends on (the `inputs` it names, and an
instrument's tool, files and device identity), each output file of the source, and the workspace's stamp. Each output
file is one sample: its SEMANTICS are the source, the file's name, the inputs' digests (and an instrument's arguments),
so the same output of the same inputs has the same key; its PROVENANCE is the run and the stamped commit. The store is
ASKED whether a sample of that key already holds the outcome (`Store.put(..., unless_stored=True)`), and nothing is
stored when one does: a second run over the same build stores
nothing, into whatever store it is pointed at. An instrument's refusal (`{"refused": WHY}`) is stored as a failed sample
naming why. A source that is an instrument on the device runs here first (`fsla_devtools.instrument`), in the run phase,
which holds no host budget; its output is written into the scratch directory and deleted once stored.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from .config import machine
from .instrument import REFUSED, run
from .results import Store, run_id


def semantics(record: dict, output: dict) -> dict:
    """What one output file is: its source, its name, the digests of what it depends on, an instrument's arguments."""
    blob = {"source": record["source"], "file": output["name"], "inputs": record["inputs"]}
    if record.get("instrument") is not None:
        blob["args"] = record["instrument"]["args"]
    return blob


def outcome(record: dict, path: Path) -> dict:
    """What `Store.put` is handed for one output file: an instrument's refusal as the error, else the file."""
    if record.get("instrument") is not None:
        produced = json.loads(path.read_text())
        if isinstance(produced, dict) and REFUSED in produced:
            return {"error": f"refused: {produced[REFUSED]}"}
    return {"output_file": path}


def store(record: dict, files: dict[str, Path], *, root: str | None, run: str, scratch: Path) -> list[dict | None]:
    """Each output stored at the store target's label (one sample an output, its run the run's, `.<i>` after it where
    the source builds several), a copy of its file moved in; None where the label holds it."""
    provenance = {"run": run, "checkouts": {record["source"]: record["stamp"]}}
    stored = []
    several = len(record["outputs"]) > 1
    for i, output in enumerate(record["outputs"]):
        copy = scratch / "stored" / output["name"]
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(files[output["name"]], copy)
        sample = Store(record["label"], root).put(semantics(record, output), provenance=provenance,
                                                  run=f"{run}.{i}" if several else run, unless_stored=True,
                                                  **outcome(record, copy))
        copy.unlink(missing_ok=True)
        stored.append(sample)
    return stored


def produce(record: dict, runfiles: Path, scratch: Path) -> dict[str, Path]:
    """Each output file of the record's source: a build output in the runfiles, or what a device instrument writes when
    it runs now."""
    instrument = record.get("instrument")
    if instrument is None or not instrument["device"]:
        return {o["name"]: runfiles / o["path"] for o in record["outputs"]}
    (output,) = record["outputs"]
    out = scratch / output["name"]
    out.parent.mkdir(parents=True, exist_ok=True)
    files = [(path, str(runfiles / source)) for path, source in instrument["files"]]
    run(files, instrument["tool"], instrument["args"], out, python=sys.executable, device=True)
    return {output["name"]: out}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m fsla_devtools.record")
    ap.add_argument("--runfiles", required=True, help="the run target's runfiles directory")
    ap.add_argument("--record", required=True, help="the record's description, by its path in the runfiles")
    ap.add_argument("--store-root", help="the store's records directory (default: the store's own)")
    ap.add_argument("--no-store", action="store_true", help="produce the outputs and keep them, store nothing")
    ap.add_argument("--scratch", help="where a device instrument's output is written (default: the dev config's "
                                      "host.scratch)")
    a = ap.parse_args(argv)
    runfiles = Path(a.runfiles).absolute()
    record = json.loads((runfiles / a.record).read_text())
    run = run_id()
    scratch = Path(a.scratch) if a.scratch else Path(machine("host.scratch")) / "record" / run
    files = produce(record, runfiles, scratch)
    if a.no_store:
        for name, path in files.items():
            print(f"not stored: {name} ({path})")
        return 0
    for output, sample in zip(record["outputs"], store(record, files, root=a.store_root, run=run, scratch=scratch),
                              strict=True):
        where = f"{record['label']} {output['name']}"
        print(f"already stored: {where}, the record holds this outcome" if sample is None else
              f"stored: {where} sample {sample['run']}" + ("" if sample["ok"] else f" ({sample['error']})"))
    shutil.rmtree(scratch, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))


__all__ = ["main", "outcome", "produce", "semantics", "store"]
