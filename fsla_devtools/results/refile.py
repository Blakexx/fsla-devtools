"""THE RE-FILING: bytes kept before the store held them as samples -- files a data repository kept as written (a
BACKFILL), or samples of the store's earlier layout (a MIGRATION) -- written as samples of the labels that produce them,
each output and part byte-identical, and a table proving every stored byte the kept file's own.

    python -m fsla_devtools.results --root RECORDS refile --plan PLAN --from CHECKOUT --table TABLE

The PLAN is JSON, written by whoever knows what the files mean (the store knows nothing of a tool's files):
`{"samples": [{"label", "run", "semantics", "at", "provenance", "mark", "output" or "error", "parts": {NAME: FILE},
"extra"}]}` -- `output` and each part a path inside CHECKOUT (the data repository at the commit the mark names), `at`
the date the bytes state, in the store's form (`YYYY-MM-DDTHH:MM:SSZ`), `run` the sample's name
(`<YYYYMMDDTHHMMSSZ>-<token>`), `provenance` and `extra` what the bytes state of where they came from and what else, and
`mark` where they were kept: `{"backfilled": {"file", "commit"}}` or
`{"migrated": {"from", "commit"}}` (`Store.refile`). A run the label already holds is written again by nothing and
checked all the same. The TABLE is one tab-separated line a file (the output and each part): the file, its sha256, the
label, the run, the stored file (under the records directory), its sha256, and `identical` -- `yes` only where the two
digests are equal; the re-filing fails when any is not.
"""
from __future__ import annotations

import json
from pathlib import Path

from .store import Store, digest

#: the table's columns
COLUMNS = ("file", "sha256", "label", "run", "stored", "stored_sha256", "identical")


def refile(plan: dict, checkout: Path, root: Path) -> list[dict]:
    """Every sample of `plan` written under `root` and every file's row of the table."""
    rows = []
    for entry in sorted(plan["samples"], key=lambda e: (e["label"], e["run"])):
        store = Store(entry["label"], root)
        parts = entry.get("parts") or {}
        store.refile(entry["semantics"], run=entry["run"], at=entry["at"], provenance=entry["provenance"],
                     mark=entry["mark"], output_file=checkout / entry["output"] if "output" in entry else None,
                     error=entry.get("error"), extra=entry.get("extra"),
                     parts={name: checkout / path for name, path in parts.items()} or None)
        sample = json.loads((store.dir / f"{entry['run']}.json").read_text())
        stored = [(entry["output"], store.output(sample))] if "output" in entry else []
        if not stored and not parts:
            (where,) = entry["mark"].values()
            rows.append({"file": where.get("from") or where.get("file"), "sha256": "", "label": entry["label"],
                         "run": entry["run"],
                         "stored": str((store.dir / f"{entry['run']}.json").relative_to(root)), "stored_sha256": "",
                         "identical": "yes" if sample.get("error") == entry["error"] else "no"})
        stored += [(path, store.parts(sample) / name) for name, path in sorted(parts.items())]
        for file, dest in stored:
            mine, theirs = digest(checkout / file), digest(dest)
            rows.append({"file": file, "sha256": mine, "label": entry["label"], "run": entry["run"],
                         "stored": str(dest.relative_to(root)), "stored_sha256": theirs,
                         "identical": "yes" if mine == theirs else "no"})
    return rows


def write_table(rows: list[dict], path: Path) -> None:
    path.write_text("\t".join(COLUMNS) + "\n" + "".join("\t".join(str(r[c]) for c in COLUMNS) + "\n" for r in rows))


def run(plan_path: Path, checkout: Path, root: Path, table: Path) -> int:
    rows = refile(json.loads(plan_path.read_text()), checkout, root)
    write_table(rows, table)
    different = [r["file"] for r in rows if r["identical"] != "yes"]
    labels = {r["label"] for r in rows}
    samples = {(r["label"], r["run"]) for r in rows}
    print(f"refile: {len(rows)} files, {len(labels)} labels, {len(samples)} samples; "
          f"{len(rows) - len(different)} byte-identical, {len(different)} not")
    for file in different:
        print(f"refile: {file} is not stored byte-identical")
    return 1 if different else 0


__all__ = ["COLUMNS", "refile", "run", "write_table"]
