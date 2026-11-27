"""fsla_devtools.results: the interface every tool stores its measurements through, and the readings of them.

The store is a durable index of what Bazel's targets produced (Blake, 2026-11-27): a tool opens a STORE at the LABEL of
the target that produced what it stores (`Store("@fsla//measure:session_entmax")`) and hands it what it measured. A
SAMPLE is one execution: the SEMANTICS the tool handed over (what ran: its inputs, their identities, its parameters) and
their digest, the KEY; its raw output or its failure; when, how long; and its PROVENANCE (which checkout, which commit,
which host). The key is sha256 over the semantics, so a reader groups the samples of one measurement however many times
and wherever it ran, and a changed input is another key. Every sample is kept, failures included; nothing derived from
two samples (a ratio, a baseline) is stored.

The backend is a directory of files, `<workspace>/<package>/<target>/<run>.json` a sample, its output and the files the
same execution wrote beside it, its PARTS, in `<run>/` beside it: by default the data repository's `records/`, at the
checkout the dev config's `store.root` names. The readings -- the index and its views, the verdict, the dashboard --
are queries over it, never stored.
"""
from .store import Store, backend, check, checkout, digest, key, label_path, labels, machine_paths, outputs, portable, run_id

__all__ = ["Store", "backend", "check", "checkout", "digest", "key", "label_path", "labels", "machine_paths", "outputs",
           "portable", "run_id"]
