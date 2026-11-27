"""fsla_devtools.results: the interface every tool stores its measurements through, and the readings of them.

A tool opens a STORE at its own LOCATION (`Store("fsla/sass")`, `Store("timing/session")`) and hands it what it
measured. A RECORD is everything kept under one KEY: the SEMANTICS the tool handed over (what ran: its inputs, their
identities, its parameters), and every SAMPLE -- one execution under that key, its raw output or its failure, when, how
long, and its PROVENANCE (which checkout, which commit, which host). The key is sha256 over the semantics, so the same
measurement lands on the same record however many times and wherever it runs, and a changed input lands on a new one.
Every sample is kept, failures included; nothing derived from two records (a ratio, a baseline) is stored.

The backend is a directory of files, `<location>/<key>.json`, each sample's output beside it as
`<key>.<n>.out<suffix>`: by default the data repository's `records/`, at the checkout the dev config's `store.root`
names. The readings -- the index and its views, the verdict, the instruments' deltas, the dashboard -- are queries over
it, never stored.
"""
from .store import Store, backend, check, check_files, checkout, digest, key, locations, machine_paths, outputs, portable

__all__ = ["Store", "backend", "check", "check_files", "checkout", "digest", "key", "locations", "machine_paths", "outputs",
           "portable"]
