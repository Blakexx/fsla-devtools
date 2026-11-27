# fsla-devtools

The development tools every FSLA repository shares. Standard library only, so every venv, CI job and container can
carry it. It is a development dependency: nothing a user installs imports it.

| Module | What it is |
|---|---|
| `fsla_devtools.mirror` | the declarations (`.github/declarations.json`), their check and the private-shaped content the tripwire refuses, run by each repository's commit gate |
| `fsla_devtools.tripwire` | the content checks over a staged diff or a pushed range, run by each repository's pre-commit and pre-push hooks |
| `fsla_devtools.docrefs` | the doc references: every citation a repository's docs make of its tree pinned to the content it was read against, run by each repository's `docrefs_test` |
| `fsla_devtools.cells` | the central cell registry: every input an FSLA measurement or test runs on (carry, layer, producer, QKV), its draw, its seed and the regime it proves, named once; bases and the cells derived from them; each cell's DESCRIPTION (its record and the digest of the code that draws it), a Bazel target (`@<cells>//:<cell>`), and its fields (`@<cells>//:cells.bzl`) |
| `fsla_devtools.timing` | the timing system: the session program a Bazel run target starts (`fsla_devtools.timing.session`: interleaved, clock-locked, stored), over entries of one or several commits' bundles; its null gate and the memory pass; the entry workers |
| `fsla_devtools.diff` | the diff: two sides over the same cells, each produced in its own bundle (`side`), compared under one rule (bit-identity, the per-slot clause rule, support equality) by a verdict (`verdict`), with a claim a gate can fail on -- a difference expected or refused |
| `fsla_devtools.results` | the results store's code: one file a sample under its producing target's label, every sample kept, in the data repository (fsla-results); its index and views, the verdict (whether a candidate's timing is a regression against a reference timed in the same sessions), the instruments' deltas and the dashboard |
| `fsla_devtools.instrument` | an instrument run: a tool over the files it reads, laid out as a tree of copies, its JSON (or its refusal) the output; what the `instrument` rule's action runs, and a store target's run for an instrument on the device |
| `fsla_devtools.record` | the record of a build output: the program a `results_store` run target starts, storing each output file unless its record already holds it |
| `fsla_devtools.process` | `subprocess.run` for a command that starts processes of its own: a timeout or an interrupt stops the whole tree |
| `fsla_devtools.config` | the dev config reader: one directory of JSON files a section, every key declared; the machine's `host`, `clock` and `store` sections every repository shares |
| `fsla_devtools.locks` | the machine's locks: the GPU lock (exclusive or shared), the host-compute budget and named file locks, the SM clock lock (the machine's, counted by its holders) |
| `fsla_devtools.graph` | the build graph's shared machinery: the front door every repository's Bazel runs through, the environment's store builder, the bundle tag's export of another workspace's entries, the test tiers' sharding plugin |
| `fsla_devtools.disk` | the machine's disk, held by the front door: the cap on live worktrees and their removal with their output bases (`bazel --worktree`), the free-space floor under every build, the trim of the repository cache's unused downloads |

The `fsla_devtools` Bazel module (`MODULE.bazel`, `bazel/`) is the rest of the build graph's machinery: its
environment extension, generic rules and measurement rules (below).

## The declarations and the tripwire

Every FSLA repository is developed in public, with its whole history. `.github/declarations.json` in a repository
declares every tracked path (`ships` lists path prefixes; a path is declared when it is an entry or falls under one),
and `python -m fsla_devtools.mirror --check` refuses a tracked path under no entry, at commit time. Its other fields are
the tripwire's own: `allow` names shapes the repository's files may carry, each with its reason, `emails` the addresses
a file may carry (the author's), and `agent_files` whether `.claude/` and `CLAUDE.md` are refused at any depth (default
true; a code repository whose agent configuration is public declares `"agent_files": false`).

**The tripwire** refuses a private-shaped string: a home directory, the private scratch repository's name, an email
address other than the declared `emails` (`noreply` and `example` addresses never count), an IPv4 address (loopback and
the documentation ranges excepted), a cloud project or bucket identifier, a provider account or instance id, a ledger
event id, or a credential (GitHub, Hugging Face, Anthropic and AWS token shapes, a private key block); a credential can
never be allowed. Every pattern is spelled so that the tool's own text does not match it. `SCRATCHPAD.md` is always
refused. The checks run over the lines a change adds, so a violation is refused before it is history:
`python -m fsla_devtools.tripwire --staged` in the pre-commit hook, `--range A..B` (any `git log` revisions) in the
pre-push hook, over every commit patch by patch, so a secret added and removed inside the push still refuses it; `--tree`
reads HEAD whole. A refusal is a line `TRIPWIRE REFUSED: path:line: the check`.

```bash
python -m fsla_devtools.mirror --check       # every tracked path declared
```

## The doc references

A doc that cites the tree is a claim about it; `fsla_devtools.docrefs` pins each citation to the content it was last
read against, so a change to the cited code shows as the docs it makes stale. A REFERENCE is a citation in code font or
a link: `fsla_devtools/mirror.py`'s `scan`, a dotted name whose first component is one of the repository's package
roots (`fsla_devtools.docrefs.verdict`), a path (`fsla_devtools/tripwire.py`), a path into the pinned data (the path
prefixed "data:"), or a markdown link ([the gate](#tests-and-the-gate)); a fenced block cites nothing. Its TARGET
is a Python function, method, class or constant (read by `ast`, following re-exports), a C++/CUDA function or
`//: @ref NAME` ... `//: @end` region, a markdown section (its heading to the next at its level or above), or a whole
file or directory, checked only to exist. A target is hashed normalized -- Python through `ast.unparse` with its
docstrings dropped, the rest with comments dropped and whitespace collapsed -- so a reformat or a comment edit moves
no pin.

The lock holds one pin per (doc, target): the digest, the commit it was pinned at, and whether a docs pass has read it.
The gate fails on any reference not clean: one the lock does not hold, one whose target changed or vanished (PENDING),
one never read, one that resolves to nothing, and a pin no doc makes any more. Each line names the citing doc:line and
the target, and the run ends with the one command that re-pins once the sentence has been read against the target. The
lock records the interpreter's version, since `ast.unparse` moves between versions; a run under another is refused.

```bash
bazel test //:docrefs                        # the gate
bazel run //:docrefs -- pending              # each pending target, its citing docs, and its change since the pin
bazel run //:docrefs -- accept DOC...        # pin DOC's new and changed references, after reading them
bazel run //:docrefs -- accept --whole DOC   # every reference in DOC was read
bazel run //:docrefs -- prune                # drop the pins no doc makes
bazel run //:docrefs -- init                 # pin today's references, unread; refused when a lock exists
bazel run //:docrefs -- move OLD NEW         # a path moved: citations and pins follow, refused unless the content is the same
```

A repository turns it on with a `docrefs_test` (`bazel/rules.bzl`), whose attributes are its configuration:

| Attribute | What it is |
|---|---|
| `lock` | the lock's path in the checkout |
| `packages` | the package roots a dotted name resolves under, each to the directory it imports from (`""` for the root) |
| `mirrors` | MIRROR RULES, in order: a doc under the key's prefix describes the source at the value's prefix and the doc's stem (`docs/internals/` -> `csrc/src/`: `docs/internals/a/b.md` mirrors `csrc/src/a/b.cu`), and on it a bare name in code font cites that source's primitive |
| `exclude` | docs never read: a ledger whose rows describe the tree at their commit |
| `data` | the pinned data's `:root` (the environment extension's `data` tag): a citation of the pinned data resolves in it |
| `python` | the interpreter: an environment's `:python`, or an executable that runs one |
| `tool` | the tool: the environment extension's `docrefs` repository's `:docrefs.py` |
| `anchor` | the workspace's `MODULE.bazel` |

The test runs in the checkout, outside the sandbox and never cached: its input is the tracked tree and its history.
This repository's own (`//:docrefs`, in the gate) pins this README's citations in `references.lock.json`.

## <a id="timing"></a>The timing system

**A SESSION IS A BAZEL RUN TARGET** (`bazel/measure.bzl`). A `timing_bundle` is the code a tree's entries run in: its
runfiles (the code they import, what that code reads, files `layout` puts where the code reads them), the modules a
worker must load from inside it (`binds`), and `<name>.bundle.json`, digested once for every entry over it: the
workspace's stamp (the commit, branch, checkout name and uncommitted diff digest its `--workspace_status_command` writes
as `STABLE_GIT_COMMIT`, `STABLE_GIT_BRANCH`, `STABLE_GIT_DIFF_SHA256`, `STABLE_CHECKOUT`; a workspace that stamps none is
refused), the digest of every file the bundle holds, and its environment. A `timing_entry` is one executor
(`module:function`, called with a cell's data and the entry's `params`, returning a `fsla_devtools.timing.Timed`: set
up, call, reset, one stopwatch) in a bundle, on cells named as targets (`cell`, which the environment extension's
`cells` tag declares one of for every central cell: its description, never its tensors; the same tag writes
`cells.bzl`, every cell's fields, so a macro selects cells by their facts). `kind = "clock"` is the in-kernel clock read
that proves a session's lock. An `interleaved_timing` names entries, a clock reader, `rounds`, `reps`, `warmup`, its
`workspace` (the session is stored at its own label, `@<workspace>//<package>:<name>`) and a `claim`, and `bazel run`
starts the SESSION PROGRAM (`python -m fsla_devtools.timing.session`, on the environment's store venv, `venv`) on its
manifest:

```bash
bazel run //measure:session_entmax                     # measure, store one sample at @fsla//measure:session_entmax
bazel run //measure:session_entmax -- --no-store       # measure, keep session.json in the scratch, store nothing
bazel run //measure:session_entmax -- --store-root DIR --scratch DIR
```

The program takes the GPU lock exclusive, then the host budget whole, then the machine's clock lock, and lowers no
priority; refuses a host whose dev config declares no clock lock unless the claim is `unlocked`, and a locked host's
session without a clock reader; starts one entry worker per bundle, on the bundle's interpreter with the bundle's
runfiles as its whole import path; sets every entry up on each of its cells behind a barrier, each worker reporting the
digest of the code it draws cells with and the file each module the entry `binds` was loaded from -- a foreign draw or
a module loaded from outside the bundle is that member's failure; refuses two stopwatches; proves the clock; warms each
member past `WARMUP_FLOOR` calls and times rounds x reps in a fresh order each rep, seeded from the run's identity (the
seed is stored); reads the clock again. A session in which no member set up fails, naming the first refusal. It stores
ONE sample: the semantics are what ran (each entry's executor, parameters, cells, bundle digest and environment, the
timing parameters, whether the clock was locked, the machine's devices), the provenance each bundle's stamp. A failed
session stores nothing and keeps its samples in the scratch directory, named. A second form of an entry (one call
captured once and replayed) is another executor over the same bundle and cells, declared as its own target, so its label
is never its eager twin's.

THE NULL GATE is `interleaved_timing(null_gate = True)`: the same session with every member set up twice, each copy in
a worker of its own (`<owner>#a`, `<owner>#b`), stored at its own label with one row an entry on a cell -- the per-rep
ratios of the copies, a over b, and `trusted` where their interquartile range holds one. A comparison across workers
is read beside it: the verdict (`fsla_devtools.results.verdict`) reports each unit's gate, and does not judge a unit
whose gate found a worker's bias. THE MEMORY PASS is `measure_memory(entries, calls)`: each member alone under the GPU
lock and the host budget, the caching allocator's peak over its calls (each after its reset), what stayed allocated
and what it holds outside the allocator, stored at its own label.

## <a id="bundles"></a>Bundles: another commit in the same session

A timing compares only inside the session that interleaved it, so two commits are entries of ONE run. Each commit is
built in ITS OWN workspace by its own pins, and the environment extension's `bundle` tag consumes what it built:

```python
environment.bundle(name = "reference", commit = SHA, remote = "https://github.com/Blakexx/fsla.git")
environment.bundle(name = "candidate", worktree_env = "FSLA_CANDIDATE")    # --repo_env=FSLA_CANDIDATE=PATH
```

The repository rule (`fsla_devtools.graph.export`) checks a commit out once under the store (`$FSLA_GRAPH_STORE/
checkouts/<sha>`: a shallow fetch of the commit) and builds it through its own front door WITHOUT the machine's
fsla-devtools override (`FSLA_GRAPH_OWN_PINS`), so an old commit never builds against today's rules, and shuts its
server down after; a local checkout (`worktree_env`: a module names no machine path, so the run names the checkout) is
built in place with the override, again every invocation, incrementally. Either way `bazel cquery` reads each
`_timing_entry` target of `packages` (default `//measure:all`: entries, clock readers and diff sides) and its runfiles
(`export.cquery`), `bazel build` builds them, and every file is COPIED into the repository's `tree/` -- a link into
another output base dangles once that base is cleaned. The repository holds one `exported_entry` a target, by the
target's own name (`@reference//:clock`), carrying the same provider as `timing_entry` with its paths this workspace's,
and `exports.bzl` (`ENTRIES`: each target's kind, executor, parameters and cells by name) for a workspace's macros. A
record names the entry by the role the workspace gives its repository (`@reference//:...`) and by its bundle's digest
and stamp, so two runs of one commit are samples of one record. A commit older than the `timing_entry` rule cannot be
a bundle. The costs: a fetch is a whole Bazel build in the other workspace (its failure is the fetch's), and one
library compile a commit a machine unless the disk cache holds it.

## <a id="instruments"></a>The instruments and the records of build outputs

**AN INSTRUMENT** (`instrument`, `bazel/measure.bzl`) is a tool's JSON over the files it reads: `tool` (a `.py` run on
the environment's interpreter as `TOOL ARGS... --json OUT`), `arguments` (`$(rootpath ...)` expanded), `data`.
`fsla_devtools.instrument` copies every file to its path in a fresh tree, links followed, so a tool that resolves its
own path finds the tree and never the checkout the files link into, and runs the tool there with the tree as its import
path. A tool that cannot run on what it was handed writes `{"refused": WHY}` and exits 0: the refusal is its output and
stops nothing else. A tool that fails, or exits 0 without its JSON, fails. Without a device an instrument is an action
(outside the sandbox, since the environment's interpreter reads the store), its JSON a build output; it sees no device
and no machine's home. ON THE DEVICE (`devices`, the devices' identity, which keys its record) it is never an action: a
build holds the host budget, and the GPU taken under it deadlocks against a measurement, which takes the GPU first, so
it runs in the run phase of the store target that records it, in the run's environment, taking the machine's locks
itself.

**A BUILD OUTPUT'S RECORD** (`results_store`): `src` (any target's files, or an instrument), its `workspace` (its samples
are stored at the store target's own label), and `inputs` (what the output depends on beyond an instrument's own). The build writes `<name>.record.json`: the source's
label, each output file, the CONTENT DIGEST of every input (each of its files by its path and its bytes' digest) and the
workspace's stamp, refusing a workspace that stamps no commit. `bazel run` starts `fsla_devtools.record`, which stores
each file as one sample at the store target's label -- its SEMANTICS the source, the file's name, the inputs' digests
and an instrument's arguments; its PROVENANCE the run and the stamped commit -- and ASKS THE STORE first
(`Store.put(..., unless_stored=True)`): when a sample of that key already holds this outcome nothing is stored, so a second run over the same build stores nothing into
whichever store it is pointed at. An instrument's refusal is a failed sample, `refused: WHY`. A record names another
repository by its own name (`@warpline`, `@host_gpu`), never the canonical name Bazel derives, which moves with Bazel's
release and the module graph.

```bash
bazel run //measure:store_static_manifest                  # record each static manifest, or nothing if held
bazel run //measure:store_oracle_tier                      # run the oracle tier on the device, then record it
bazel run //measure:store_oracle_tier -- --no-store        # run it, keep its JSON in the scratch, store nothing
bazel run //measure:store_static_manifest -- --store-root DIR
```

## <a id="the-diff"></a>The diff

"The same function in two commits" and "the kernel against its fp64 reference" are the same question asked twice, so
`fsla_devtools.diff` asks it once. A `diff_side` (`bazel/measure.bzl`) is one side of a comparison: an executor
(`module:function`, given a cell's description, returning `{name: tensor}`) over cells in a bundle, `device` for one on
the device; another commit's side is an `exported_entry` of its bundle. `diff_verdict` takes two sides and one named
STRATEGY -- `bit-identical` (a change that claims to have moved no number), `per-slot` (the oracle's clause rule: a slot
of size `s` may be off by `max(r*s, a)` under each `(r, a)` clause of its output kind, and by `rtol * envelope` where
the output is multilinear, the smallest term binding), `support-equal` (which slots survive is the claim, then their
values) -- and states what it EXPECTS: `same` is a gate on an invariant, `different` is the non-vacuity half, where a
mutant that goes unseen is the failure. The verdict is an INSTRUMENT whose tool is `fsla_devtools.diff.verdict`: it
starts `fsla_devtools.diff.side` for each side on the side's bundle's interpreter, in the bundle, and compares what they
produced. Over host sides it is an action, cached on both bundles and the comparing environment, its sides at
`threads` torch threads; over a side on the device it runs in the run phase of its store target, or in its test,
holding the GPU and the host budget. `diff_test` is its gate: a claim not held fails it, or, with
`on_difference = "record"`, is printed and passes; `results_store` records the verdict.

Two lessons from the tool this replaces are built in rather than left to arrangement. A side NAMES the library it is
supposed to be running (`binds`, the bundle's) and proves it: a side that imported its library from outside its bundle
would compare another tree, so the side fails; and two sides with one bundle and one executor compared one tree with
itself, which is refused. A verdict states the COUNT it needs (`minimum`): a comparison that compared less than it
declared does not hold its claim, because a gate that cannot run says so instead of reporting a verdict over whatever
survived. The raw never leaves the run: each side's tensors go to the verdict's scratch, and what it outputs is the
DIFF -- per cell and quantity the worst slot, where it is, what bound it, and how many slots were compared. A cell
either side could not produce is not a comparison that passed: it is counted as unusable and the claim does not hold;
a cell both sides refused is listed, not counted.

```python
diff_verdict(name = "diff_oracle", left = "@candidate//:side_oracle", right = "@reference//:side_oracle",
             strategy = "bit-identical", minimum = N, venv = VENV)
diff_test(name = "diff_oracle_test", verdict = ":diff_oracle", venv = VENV)
```

## The results store

Every measuring tool stores what it measured through one library, `fsla_devtools.results`; the records themselves are
the data repository's (fsla-results, a repository of data and no code), at the checkout the dev config's `store.root`
names. The store is a durable index of what Bazel's targets produced (Blake, 2026-11-27: "whatever the thing you ran to
get that output is the key itself"): a sample is filed under the LABEL of the target that produced it.

```python
from fsla_devtools.results import Store, checkout, key, run_id

store = Store("@fsla//measure:store_oracle_tier")  # the producing target's label, under store.root's records/
store.put(semantics, output=rows, wall_s=12.3, provenance=checkout(worktree), run=run_id())
store.put(semantics, error="RuntimeError: ...")    # a failure is a sample too
store.put(semantics, output_file=path, unless_stored=True)   # None, storing nothing, when a sample of the key holds it
store.put(semantics, output_file=result, parts={"trace/row0.json": row})   # the files the same run wrote beside it
store.put(semantics, output_file=session, provenance=provenance, timing=True)  # a timing: refused without its commits
store.samples(key(semantics))                       # newest first; store.output(sample), store.parts(sample)
Store(label, root, scratch=True)                    # a run not recorded (--no-store): takes an uncommitted tree
```

- **A stored number names a commit** (Blake, 2026-11-27): a sample whose provenance names a checkout with uncommitted
  changes (a `diff_sha256`) is refused, a timing or not; a measured variant is a commit. Only a `scratch` store, the one
  a tool keeps for a run it does not record, takes one.

- **A label** names who produced it: `@<workspace>//<package>:<target>`, the workspace the repository's module name
  (`@fsla//measure:session_entmax`, `@fsla//measure/lab:add_rate.run`, `@fsla//:container_check`). Every store rule
  passes its own (`workspace = native.module_name()`); a program that stores names the target it is run by.
- **A sample** is one execution, one file named by its RUN (`run_id()`: `<YYYYMMDDTHHMMSSZ>-<8 hex>`, so a folder sorts
  oldest first): its SEMANTICS (what it is a measurement of) and their digest, the KEY; its output (JSON, or a file moved
  in) or its error; when, how long; its provenance (the checkouts and commits the tool names; the host); and its PARTS
  -- the files the same execution wrote beside its output (a traced row, a profiler's dump), each moved in under its
  name (a relative path of plain names) and listed in the sample's `parts`.
- **The key** is sha256 over the semantics: a reader that compares groups a label's samples by it, and a changed input
  is another key.
- **A sample is written once**: its folder and then its file are created exclusively, so no lock is taken, no file is
  rewritten and no lock file is ever in the tree; a run that exists is refused.

    records/<workspace>/<package>/<target>/<run>.json            a sample (format 2: format, label, key, semantics, run,
                                                                 utc, ok, wall_s, provenance, output or error, parts)
    records/<workspace>/<package>/<target>/<run>/output.<ext>    its output
    records/<workspace>/<package>/<target>/<run>/<name>          its parts

Nothing derived from two samples (a ratio, a baseline) is stored: that is the reader's. **A record carries no machine
path**: a path inside a home directory in anything stored is refused at `put` -- a person's, a container's or a rented
box's alike -- since a record is read on other machines and published; a file is named by its path inside its checkout
(`portable(text, *checkouts)` rewrites a message) and a checkout by `checkout()`, its directory's name, branch, HEAD and
the digest of its uncommitted diff. A git call that fails is a failure, never "clean, no commit". **A timing names its
commits**: a sample stored with `timing=True` whose provenance's `checkouts` does not give every checkout's `git_sha` is
refused. A MACHINE PATH is a path inside someone's home directory (`/home/<user>`, `/Users/<user>`, `/root`, a Windows user
folder); a system path (`/opt/venv/bin/python`) is a fact of the environment and is kept. The rule is the writer's, refused
where a sample is written: `check(root)` holds every file to a sample under its label's folder or in a sample's folder,
every sample to its label, its run, its format and a key that recomputes, every success to its output and its parts and
every failure to its error, refuses a lock or a write in progress and a target's folder under another's, and reads no
sample's output, since a stored sample is never rewritten.

**A RE-FILED SAMPLE** is bytes kept before the store held them, written as a sample of the label that produces them
(`Store.refile`, and `refile`, `fsla_devtools/results/refile.py`, over a plan of many): a file a data repository kept as
written (`backfilled`), or a sample of the store's earlier layout (`migrated`). Its bytes are copied in unchanged as
the output and the files they belong with as its parts, its `utc` the date they state, its provenance what they state
(no host: the machine that re-files ran nothing), and the sample marked with where they were kept and the data
repository's commit, and the machine paths the bytes carry, stated, never refused, since nothing is rewritten. A reader
tells a re-filed sample from one the store wrote at run time by that mark. The plan is written by whoever knows what
the files mean -- the store knows nothing of a tool's files: each sample's label, run, semantics, date, provenance,
mark, output and parts. A run the label already holds is written again by nothing, and the re-filing writes a table, a
line a file: its sha256 and the stored file's, `identical` only where they are equal; it fails when any is not.

**The index** (`fsla_devtools.results.index`) is the samples as one SQLite database, derived: `samples(label, run, key,
ok, utc, wall_s, semantics, provenance, error, output_path, output, extra)`, a row a sample, the JSON fields read with SQLite's JSON functions, and a tool's shape a view in `fsla_devtools/results/views/`, created after
every index (a view that left the package leaves the database). A query's database is a cache of the machine
(`results/<12 hex of the records path>/index.sqlite` under `host.build_cache`), rebuilt incrementally; the data
repository's own index is a build output (`results_index`, `bazel/results.bzl`), never a file beside the records.

| view | from | one row per |
|---|---|---|
| `timing_members` | every session's samples (an output with `members` and `samples`, at its session's label) | member of a stored session: the run, the session, its entry (`owner`, a label; a registration in an older sample), the label and arm read from it, the cell, the LEVEL it prices (`kernel`, `op`, `layer`), whether it ran and why not, device, torch, the owner's commit and diff, the session's rounds, reps and clock reads |
| `timing_samples` | `timing_members` | timed call, in the order taken: its member's label, arm and cell, its round, rep and position in the rep's random order, and its milliseconds |
| `memory_rows` | every memory pass (`measure_memory`, at its label) | timing entry alone on a central cell: peak allocated and reserved bytes, what was allocated before the build and after the calls, bytes held outside the caching allocator and the totals with them, label, arm, commit |
| `instrument_cells` | every per-cell instrument's samples (a `summary.cells`, at its store target's label) | cell of an instrument run: the run, whether the tool ran on it, and its exit and error when it could not |
| `instrument_metrics` | every build output's record (semantics with `inputs`) | every NUMBER a declared instrument recorded, one row each, in long form: the run, the cell (NULL for the whole-binary `sass`), a dotted `metric` (`sass.<cubin>.<function>.<count>`) and its value |
| `diff_cells` | every diff's samples (an output with `cells`, at its target's label) | cell and quantity of a stored diff: the claim and whether it `held`, the cell's status (`ok`, `unusable`, `refused`) and why, and the quantity's verdict with why it differs and its worst slot |
| `null_gates` | every null gate (`interleaved_timing(null_gate = True)`, at its label) | entry on a cell of a null gate: the run, its owner, label and arm, whether its two workers agree, the ratio's median and quartiles, its commit |
| `cells` | fleet and local grids | stored grid cell: bench, config, cell, image or local checkouts, row |

```bash
python -m fsla_devtools.results [--root RECORDS] check   # the default root: store.root's records/
python -m fsla_devtools.results --root RECORDS refile --plan PLAN --from CHECKOUT --table TABLE
python -m fsla_devtools.results show [LABEL]
python -m fsla_devtools.results index [--db FILE]
python -m fsla_devtools.results sql "SELECT ..."
python -m fsla_devtools.results history|latest LABEL [--where PATH=VALUE]
python -m fsla_devtools.results verdict --reference LABEL [--candidate LABEL] [--cell C] [--arm A]
python -m fsla_devtools.results instruments --baseline RUN [--run RUN]
python -m fsla_devtools.results dashboard --out suite.html [--reference LABEL] [--run RUN]
```

**The verdict** (`fsla_devtools.results.verdict`) is a query, never a stored row. Timing is comparable only within the
session that interleaved it, so it reads within-session quantities: `session(candidate, reference)` takes one session's
two arms, each its samples by round, and gives per round each arm's median, their ratio and their difference.
`classify(sessions)` calls the last of a candidate's sessions against one reference a regression only when three gates
fire: the session's median ratio is above one plus three sigmas of its per-round ratios' own spread (sigma from the
interquartile range, never below what the stopwatch resolves, never a flat percentage); an exact Wilcoxon signed-rank
test over its per-round differences (alpha 0.01, at least 8 non-zero differences, the floor the alpha itself sets: a tied
round is dropped, so fewer is `insufficient_data`, never `same`) says the candidate is slower; and the violation
persists, a trailing run of at least two sessions over their own limits. Where neither arm shows the stopwatch's
resolution (each arm's calls on one value), the gap between the arms is the only floor and the pair is `cannot_resolve`.
Anything less is reported as what it is: `flagged_not_confirmed`, `suspicious`, `insufficient_data`, `cannot_resolve` or
`no_regression`. `verdicts(root, reference=LABEL)` pairs, in every stored session, each other label's member with the
reference label's member of the same arm on the same cell; a unit is the arm, the cell, the two labels, the device and
the code each ran (commit and diff). A member that names no commit is not read; a pair whose members price different
LEVELS is refused, alone, as a row of its own, and the rest are judged.

A member the dashboard shows that failed shows its failure, never a median of the samples it kept.

## The central cell registry

A cell is an input, named once for every package, in ONE FORM for every kind (`fsla_devtools.cells.form.Cell`):
`fsla_devtools/cells/carry.json` (a sparse recurrence's routing amplitudes, gain, values and the state it enters with,
including the state's backing), `layer.json` (a layer's hidden states and values, and the routing and logit gain its
producer is built with), `producer.json` (one routing level's logits at a logit gain) and `qkv.json` (attention's
queries, keys and values). A cell's routing is stated in FSLA's own terms -- the topology, what the write side does on
each level, what each read head of a group does -- so a layer and a carry cell state it the same way. A cell's NAME is
computed from its fields by one function (`form.name`: `carry-alt-k4-256x256-T1024`, `layer-dense-softmax-64x64-T512`,
`qkv-L1024-dv64`) and never written by hand. A record holds the input and never how a package runs it -- a kernel's
launch shape, an attention backend, a call count are an arm's -- so two arms on one cell read the same tensors, and when
the registry changes, a package that no longer fits it is what changes.

```python
from fsla_devtools.cells import cell, carry

drawn = carry.realize(cell("carry-alt-k4-256x256-T1024"))   # bf16 read/write levels, gain, v; a carried cell's entry
```

Every carry cell but the two degenerate ones declares where in the routing distribution it sits on six axes (density,
coherence, read/write correlation, mass, tail, support; `fsla_devtools.cells.regimes`), and `realize` proves the draw is
there before returning it. Every cell states the seed its data is drawn from, so a failure reproduces from the record.
A base (`bases.json`) is a fragment, never a cell; a derived cell merges the bases its `from` lists in order, and `vary`
makes one cell per value, each named by its fields. Loading, naming and checking the registry needs only the standard
library; realizing a cell needs torch.

## The dev config and the machine's locks

Every value that depends on the machine lives in one directory of JSON files, one a section: `~/.config/fsla/`, or the
directory `FSLA_DEV_CONFIG` names (a container, a test). A schema declares each section's keys with a kind, a default and
a meaning; a key a file holds that its section does not declare is refused, so a typo fails instead of silently taking
a default. `fsla_devtools.config.MACHINE` declares the sections every repository shares -- `host` (lock paths, the
compute budget, niceness, the caches and the disk's bounds: [the machine's disk](#the-machines-disk)), `clock` (the SM
clock the harness locks) and `store` (`root`, the data repository's checkout the results store writes) -- and
`machine("host.gpu_lock")` reads one value.
A repository with sections of its own (fsla's toolchain and workspace) reads its whole schema with
`read(schema, owns_directory=True)`, which also refuses a file no section declares.

The locks read only those sections, so one host and its containers share one of each:

```python
from fsla_devtools.locks.gpu import gpu_lock
from fsla_devtools.locks import host

with gpu_lock():                      # a measurement: the device to itself (mode="shared" for a correctness run)
    ...
with host.acquire(4, label="tidy"):   # up to 4 host-compute slots, at least one
    ...
```

`python -m fsla_devtools.locks.host [--slots N] [--exclusive] -- <command...>` runs a command under the budget, and
`python -m fsla_devtools.locks.gpu [--shared] -- <command...>` one under the GPU lock (a binary with no `gpu_lock()` of
its own: `cuda-gdb`, `ncu` by hand). A nested
acquire in the same process or a child is a no-op, so an outer tool that locks can start an inner tool that locks. A
shared GPU holder and the budget's CLI command lower their own priority (`host.nice`); an exclusive holder, a
measurement, never does. `with fsla_devtools.locks.clock.held():` holds the machine's clock lock the host declares: its
first holder on the machine runs the lock command and its last runs the unlock, each holder a locked file under
`host.lock_dir`, so one process exiting never unlocks the clock under another's session; the caller proves the lock with
the device's own reading (`within`).

## The build graph

Every FSLA repository builds and checks itself with Bazel, through the same machinery: this repository's Bazel module.
A repository depends on it at a public commit (`bazel_dep` plus a `git_override` in its `MODULE.bazel`, the one pin of
fsla-devtools it has).

**The front door.** bazelisk runs a repository's `bazel/bazel` (the wrapper its `.bazeliskrc` names) in place of the
Bazel it downloaded; that stub runs `fsla_devtools.graph.front_door`, found through the machine's fsla-devtools checkout,
so every Bazel command in a checkout goes through one implementation. It hands Bazel the machine's settings from the
dev config (the shared caches and the environment's store), overrides the repository's pin of this module with the
machine's checkout (an rc file of the workspace's own), so a change made here is the one every graph on the machine
runs, and holds the host budget around each build phase. It holds the machine's disk ([below](#the-machines-disk)): a
build, test or run is refused under the free-space floor, `bazel --worktree ...` is the worktree tool, and every command
first trims the repository cache's unused downloads when a trim is due. `bazel --commit-gate` is the commit gate below.

**The environment extension** (`bazel/environment.bzl`). Its tags make the repositories a graph runs on:

| Tag | Repository |
|---|---|
| `python` | a Python environment: the pinned CPython, a hash-pinned lock's packages, then packages pinned by commit and this module's `fsla_devtools`, built once a key into the machine's shared store (`fsla_devtools.graph.make_env`); `isolated_builds = False` builds the pinned packages against the environment, for one whose build imports a locked package |
| `pinned` | a release pinned by URL and digest (uv, mold, clang-tidy, Nsight Compute, Nsight Systems), an archive a .tar.gz or a .tar.xz |
| `cuda_toolkit` | a CUDA toolkit from NVIDIA's redistributables, pinned by their manifest's digest (which carries each component's) |
| `host_cc`, `host_gpu` | the host C++ compiler's and the machine's devices' identities, which actions that depend on them are keyed on |
| `tree` | the checkout's tracked files (`git ls-files`; the commit's index under the commit gate) |
| `data` | the data repository at one commit (`remote`, `commit`, the `sha256` of the commit's archive): its `records/`, as `:records`, each file by its path, and `:root` (its BUILD file, which names the root) (`bazel/data.BUILD`), nothing of its own graph -- what a workspace's citations and readers take, fixed content at the pin |
| `cells` | every central cell's description as a target (`@<name>//:<cell>`, a `cell` of `bazel/measure.bzl`) and every cell's fields (`@<name>//:cells.bzl`'s `CELLS`), generated from this module's registry on the machine's `python3` |
| `bundle` | another workspace's timing entries, clock readers and diff sides, built there by its own pins and copied in (`@<name>//:<target>`, `exported_entry`; `@<name>//:exports.bzl`'s `ENTRIES`): a commit (`commit`, `remote`) or a local checkout the run names (`worktree_env`), [above](#bundles) |
| `docrefs` | the doc references' tool as `@<name>//:docrefs.py`, a copy of `fsla_devtools/docrefs.py` a workspace's `docrefs_test` runs ([below](#the-doc-references)) |

`python`, `pinned`, `cuda_toolkit` and `data` are REPRODUCIBLE repositories (`repo_metadata(reproducible = True)`: their result
is fixed by their pins and the files they read), so Bazel unpacks each once into the repo contents cache
(`host.build_cache`'s `repository/contents`) and every checkout's output base links it; `host_cc`, `host_gpu` and `tree`
read the machine or the workspace, `cells` and `docrefs` are made from this module's source, `bundle` is another
workspace's build, and each is made per checkout.

**The environment store is bounded.** `host.store_max_gb` and `host.store_max_age_days` (the dev config) bound the store
the `python` tag builds into: each environment build sweeps, under the store's lock, the environments idle past the age
and the cached wheels nothing hardlinks, and, over the size, the oldest of them, never the environment it was asked for;
a swept environment a checkout still names is rebuilt from the cache or the index.

**The shared pins** (`pins/`, each a workspace's `@fsla_devtools//pins:<name>_pin.json`): one pin for each tool every FSLA workspace shares.

| Pin | Names |
|---|---|
| `uv_pin.json` | the uv every workspace's environment is built by, and the Python it installs |
| `cuda_toolkit_pin.json` | the CUDA toolkit (CUDA 13.5): NVIDIA's redistributable components at the release manifest's digest, what a workspace's `cuda_toolkit` tag unpacks |
| `nsight_compute_pin.json` | Nsight Compute, the profiler (`ncu`): the toolkit's manifest release of the component, by its archive's digest, what a workspace's `pinned` tag fetches and a developer's box installs |
| `nsight_systems_pin.json` | Nsight Systems, the timeline profiler (`nsys`): the toolkit's manifest release of the component, by its archive's digest, what a developer's box installs |
| `bazelisk_pin.json` | Bazel's launcher, the one tool a workspace bootstraps outside its graph |
| `mold_pin.json` | the linker a workspace's graph links with (`@mold`, a `pinned` tag) |
| `clang_tidy_pin.json` | the analyzer of a workspace's unit lints (`@clang_tidy`, a `pinned` tag): LLVM's clang-tidy as a wheel, its binary's digest checked |
| `hyperfine_pin.json`, `difftastic_pin.json` | the benchmarking tool and the structural diff (`difft`) a developer's box installs |
| `bazel_version` | the Bazel release the pins are tested with |

Each workspace keeps its own `.bazelversion`; `bazel_version_test(name, version = ".bazelversion")` (`bazel/rules.bzl`) fails
a workspace whose file differs from `pins/bazel_version`, the Bazel release the pins are tested with (this repository's own `.bazelversion` is held to it by the same test). A workspace puts it
in its gate. A `pinned` tag fetches a `.tar.gz`, a `.tar.xz`, a wheel or a bare binary by its pin.

**The generic rules** (`bazel/rules.bzl`): a check over the tracked tree (`tree_test`), a check of the commit
(`checkout_test`), the doc references ([`docrefs_test`](#the-doc-references)), a tree tool as a test (`tool_test`) or a
run target (`tool_run`), a package's part of the tree (`tree_part`), the pytest tiers (`pytest_tier`, `device_lane`)
and the Bazel release check (`bazel_version_test`). Each takes the workspace's environment as an
argument; a workspace wraps them once with its own. A check over the tree or a tier that reads another repository's
files names it in `repos` (a label of it -> a variable): its files join the runfiles and its root is exported as that
variable (`{"@fsla_data//:records": "FSLA_DATA"}`, the pinned data). **The results store's rules** (`bazel/results.bzl`): the records'
index as a build output (`results_index`), a data repository's.

**The measurement rules** (`bazel/measure.bzl`): a cell's description (`cell`), a bundle (`timing_bundle`), a timing
entry in it (`timing_entry`), an interleaved session over entries or its null gate that `bazel run` measures and stores
(`interleaved_timing`) and a memory pass (`measure_memory`), [above](#timing); another workspace's entry
(`exported_entry`, the `bundle` tag's), [above](#bundles); a diff side, verdict and gate (`diff_side`, `diff_verdict`,
`diff_test`), [above](#the-diff); an instrument (`instrument`) and a build output's record that `bazel run` stores
(`results_store`), [above](#instruments); and the workspace's stamp alone (`workspace_stamp`, `<name>.stamp.json`,
refusing a workspace that stamps no commit), the provenance of what a program run by `bazel run` measures and stores
itself (fsla's lab).

## <a id="the-machines-disk"></a>The machine's disk

A development machine fills its disk with what the checkouts leave behind: worktrees, each with a Bazel output base
(2-4 GB in fsla) that Bazel keeps after the checkout is gone, and downloads nothing deletes. Under WSL a peak is
permanent until it is undone by hand: WSL's virtual disk only grows, so its Windows drive keeps the space until the disk
is compacted. The front door holds three things against it, each read from the dev config's `host` section, none
starting a Bazel build (`fsla_devtools.disk`).

**The worktree cap.** The machine holds at most `host.worktree_cap` (16) live linked worktrees across its FSLA
repositories together: `host.repositories`, or by default every git checkout beside fsla-devtools' main checkout whose
`origin` is one of fsla, fsla-devtools, warpline, fsla-results and fsla-bench (a checkout's folder may have any name).

```bash
bazel --worktree list                          # every live linked worktree, oldest first: clean or dirty, merged or not
bazel --worktree add <path> <commit> [-b BR]   # a worktree of this checkout's repository, refused past the cap
bazel --worktree remove                        # THIS worktree, the checkout the command runs in
bazel --worktree remove <path> [--force]       # another one, by its path
```

An add past the cap is refused, and the refusal lists the worktrees that can go -- clean (no tracked change, no
untracked file), merged into their repository's default branch (`origin/HEAD`'s, else `main` or `master`) and not
locked, oldest first by when git last wrote their index -- with the command that removes one. A removal expunges the
worktree's Bazel output base first (`bazel clean --expunge` in it, which stops its server; the base is found in the
output user roots, `host.bazel_output_roots` or Bazel's default, by its `DO_NOT_BUILD_HERE`), then removes the checkout
(`git worktree remove`); a base the expunge leaves keeps the worktree. It refuses a dirty or unmerged worktree unless
named by its path with `--force`, a locked one, and a main checkout always. A repository's own worktree command creates
through `add` and keeps its own set-up steps after (fsla's `bazel run //:worktree`). The census reads git without
writing an index (`--no-optional-locks`), and an add holds the machine's `worktrees` file lock, so two adds never both
pass the cap.

**The free-space floor.** A `build`, `test` or `run` is refused before Bazel starts while a watched filesystem has less
free than `host.free_floor_gb` (30 GiB): `host.watched_filesystems`, or by default the root and, under WSL, the Windows
drive WSL's virtual disk lives on -- the mount that holds `host.windows_system32`. The refusal names each filesystem, its
free space and the floor, and what the worktree tool would free (the clean, merged worktrees and their output bases,
measured by `du`), and says when space freed inside WSL returns to the Windows drive only after a compaction. The one
override is an environment variable that says why: `DISK_FLOOR_OVERRIDE="<why>" bazel build ...` (the name the
machine's memory guard reads for its own floor). Every other command -- `clean`, `shutdown`, `query`, the worktree
tool -- runs under the floor, so a full disk can still be freed.

**The download trim.** Bazel's repository cache (`host.build_cache`'s `bazel/repository`, the machine's one
`--repository_cache`) keeps every archive a repository rule downloads, by the sha256 the rule pins it to, and deletes
none. Every Bazel command through the front door deletes the downloads (`content_addressable/`) unused for
`host.downloads_max_age_days` (30), at most once a day (a stamp's time is all it reads when none is due), under a lock
a second trimmer does not wait for, and prints one line of what it deleted. A download is safe to delete: the next
fetch that needs it downloads it again and checks it against the same pinned digest, so a deletion costs a download,
never a different result. "Unused" is the entry's `file`'s modification time, which Bazel 9.3 updates on every cache
hit. The repo contents cache beside it (`contents/`) is Bazel's own to collect (`--repo_contents_cache_gc_max_age`), and
nothing else of a cache is deleted. Null `host.downloads_max_age_days` trims nothing.

## Tests and the gate

```bash
bazel test //:gate
```

The gate is ruff, its blank-line family, the tests, the declaration check, the Bazel release check and the doc
references, on the graph's own environment. `bazel test //...` also builds the measurement rules' own targets
(`tests/rules/`: a bundle of the stub sides and entries, two diff sides, a session, its null gate and a memory pass) and
runs the verdict between the sides and its gate (`//tests/rules:planted_test`), which must see the planted difference.
The commit gate (`.githooks/pre-commit` -> `bazel/bazel --commit-gate`, every FSLA repository's hook) runs `//:gate` on
the commit as git will write it: the changes not staged set aside, the tree the commit's index.

## Publishing

This repository is developed in public: `Blakexx/fsla-devtools` is the development repository, with its whole history.

## License

Apache-2.0.
