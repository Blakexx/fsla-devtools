"""python -m fsla_devtools.results [--root RECORDS] check | refile --plan PLAN --from CHECKOUT --table TABLE |
show [LABEL] | commit -m MESSAGE | index [--db FILE] | sql QUERY | history|latest LOCATION | verdict |
instruments --baseline RUN | dashboard --out FILE"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from . import index
from .store import Store, backend, check, labels

#: the commits a sample names: a tool's own checkout at the provenance's top (`checkout()` handed whole), or each
#: checkout a measurement ran (`checkouts`)
_COMMITS = ("coalesce(json_extract(s.provenance, '$.git_sha'), (SELECT group_concat(json_extract(c.value, '$.git_sha'), ',') "
            "FROM json_each(s.provenance, '$.checkouts') c))")


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m fsla_devtools.results")
    ap.add_argument("--root", type=Path, help="the records directory (default: records/ of the dev config's store.root)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="every sample well-formed under its label's folder, its key recomputing, its output and "
                                 "parts present, no lock file")
    filled = sub.add_parser("refile", help="kept bytes made samples of the labels that produce them, byte-identical "
                                           "(refile.py)")
    filled.add_argument("--plan", type=Path, required=True, help="what each file is: its record, date and provenance")
    filled.add_argument("--from", dest="checkout", type=Path, required=True, help="the checkout the plan's files are in")
    filled.add_argument("--table", type=Path, required=True, help="the table written: each file and what it became")
    show = sub.add_parser("show", help="the labels, or one label's samples")
    show.add_argument("label", nargs="?")
    commit = sub.add_parser("commit", help="commit the records in the backend's git repository")
    commit.add_argument("-m", "--message", required=True)
    built = sub.add_parser("index", help="bring the derived SQLite database up to date (the machine's cache, or --db)")
    built.add_argument("--db", type=Path, help="the database to write (default: the machine's cache)")
    sql = sub.add_parser("sql", help="run a query on the database (tables records, samples; the views)")
    sql.add_argument("query")
    for name, what in (("history", "every sample of a label whose semantics match, oldest first"),
                       ("latest", "each matching semantics' newest successful sample of a label, with its output")):
        p = sub.add_parser(name, help=what)
        p.add_argument("label")
        p.add_argument("--where", action="append", default=[], metavar="PATH=VALUE",
                       help="a dotted path into the semantics and the value it holds (repeatable)")
    dashboard = sub.add_parser("dashboard", help="one run's timing sessions and memory rows as a standalone page")
    dashboard.add_argument("--out", type=Path, required=True)
    dashboard.add_argument("--reference", help="the label each member's paired ratio is taken to")
    dashboard.add_argument("--run", help="the run to show (default: the newest)")
    dashboard.add_argument("--session", help="one session only")
    dashboard.add_argument("--baseline", help="a reference run: each instrument metric's change against it")
    instruments = sub.add_parser("instruments", help="one run's instrument numbers beside a reference run's, as deltas")
    instruments.add_argument("--baseline", required=True, help="the reference run id")
    instruments.add_argument("--run", help="the run to read (default: the newest with instrument records)")
    instruments.add_argument("--instrument", help="one instrument only (sass)")
    instruments.add_argument("--min-change", type=float, default=0.0, help="drop rows whose relative change is below")
    instruments.add_argument("--json", action="store_true", help="one JSON object per row")
    verdict = sub.add_parser("verdict", help="each candidate judged against a reference timed in the same sessions")
    verdict.add_argument("--reference", required=True, help="the label every other label is judged against")
    verdict.add_argument("--candidate", help="one candidate label only")
    verdict.add_argument("--cell")
    verdict.add_argument("--arm")
    verdict.add_argument("--json", action="store_true", help="one JSON object per unit")
    a = ap.parse_args()

    root = backend(a.root)

    if a.cmd == "refile":
        from .refile import run

        return run(a.plan, a.checkout, root, a.table)

    if a.cmd == "dashboard":
        from .dashboard import write

        print(json.dumps(write(a.out, root, reference=a.reference, run=a.run, session=a.session, baseline=a.baseline)))
        return 0

    if a.cmd == "instruments":
        from .instruments import compare, table

        rows = compare(root, baseline=a.baseline, run=a.run, instrument=a.instrument, min_change=a.min_change)
        print("\n".join(json.dumps(r) for r in rows) if a.json else table(rows))
        return 0

    if a.cmd == "verdict":
        from .verdict import table, verdicts

        rows = verdicts(root, reference=a.reference, candidate=a.candidate, cell=a.cell, arm=a.arm)
        print("\n".join(json.dumps(r) for r in rows) if a.json else table(rows))
        return 0

    if a.cmd == "index":
        print(json.dumps(index.build(root, a.db)))
        return 0

    if a.cmd == "sql":
        columns, rows = index.query(a.query, root=root)
        print("\t".join(columns))
        for row in rows:
            print("\t".join("" if v is None else str(v) for v in row))
        return 0

    if a.cmd in ("history", "latest"):
        cond, params = index.matching(a.label, dict(w.split("=", 1) for w in a.where))
        if a.cmd == "history":
            columns, rows = index.query(
                f"SELECT s.key, s.run, s.ok, s.utc, {_COMMITS}, substr(s.error, 1, 80) "
                f"FROM samples s WHERE {cond} ORDER BY s.run", tuple(params), root)
            for row in rows:
                print("\t".join("" if v is None else str(v) for v in row))
            return 0
        columns, rows = index.query(
            "SELECT s.key, s.run, s.utc, s.semantics, s.output FROM samples s "
            f"WHERE {cond} AND s.ok AND s.run = (SELECT max(run) FROM samples t WHERE t.label = s.label AND t.key = s.key "
            "AND t.ok) ORDER BY s.run", tuple(params), root)
        for k, run, utc, semantics, output in rows:
            print(json.dumps({"key": k, "run": run, "utc": utc, "semantics": json.loads(semantics),
                              "output": json.loads(output) if output else None}))
        return 0

    if a.cmd == "check":
        problems = check(root)
        found = labels(root)
        print("\n".join(problems) or f"records: {sum(len(Store(label, root).samples()) for label in found)} samples under "
              f"{len(found)} labels, all consistent")
        return 1 if problems else 0

    if a.cmd == "show":
        if not a.label:
            for label in labels(root):
                print(f"{label:<56}{len(Store(label, root).samples())} samples")
            return 0
        for sample in Store(a.label, root).samples():
            print(f"{sample['run']}  {sample['key']}  {'ok' if sample['ok'] else 'failed'}")
        return 0

    problems = check(root)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    repo = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                          check=True).stdout.strip()
    subprocess.run(["git", "-C", repo, "add", "--", str(root)], check=True)
    return subprocess.run(["git", "-C", repo, "commit", "-m", a.message, "--", str(root)], check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
