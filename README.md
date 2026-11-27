# fsla-devtools

The development tools every FSLA repository shares. Standard library only, so every venv, CI job and container can
carry it. It is a development dependency: nothing a user installs imports it.

| Module | What it is |
|---|---|
| `fsla_devtools.mirror` | the declarations (`.github/mirror/declarations.json`), their check and the private-shaped content the tripwire refuses, run by each repository's commit gate |
| `fsla_devtools.tripwire` | the content checks over a staged diff or a pushed range, run by each repository's pre-commit and pre-push hooks |
| `fsla_devtools.cells` | the central cell registry: every input an FSLA measurement or test runs on (carry, layer, producer, QKV), its draw, its seed and the regime it proves, named once; bases and the cells derived from them; each cell declarable as a build NODE whose output is its record |
| `fsla_devtools.build` | the declared build system: targets declared by composable functions in files, keyed by what they read, cached or run in dependency order, holding the machine's resources, each in its own environment's worker |
| `fsla_devtools.timing` | the timing system, as targets: a server of entry workers, registrations of timed callables on cells, interleaved clock-locked sessions, memory passes, null gates |
| `fsla_devtools.diff` | the diff targets: two executors run over the same cells in the environments they name, compared under one rule (bit-identity, the per-slot clause rule, support equality), with a claim the build can fail on -- a difference expected or refused |
| `fsla_devtools.results` | the results store's code: records under a location, keyed by their semantics, every sample kept, in the data repository (fsla-results); its index and views, the verdict (whether a candidate's timing is a regression against a reference timed in the same sessions), the instruments' deltas and the dashboard |
| `fsla_devtools.store` | the store target: a result written through `fsla_devtools.results` as a run-stamped sample of its source's record |
| `fsla_devtools.process` | `subprocess.run` for a command that starts processes of its own: a timeout or an interrupt stops the whole tree |
| `fsla_devtools.config` | the dev config reader: one directory of JSON files a section, every key declared; the machine's `host`, `clock` and `store` sections every repository shares |
| `fsla_devtools.locks` | the machine's locks: the GPU lock (exclusive or shared), the host-compute budget and named file locks, the SM clock lock |
| `fsla_devtools.graph` | the build graph's shared machinery: the front door every repository's Bazel runs through, the environment's store builder, the test tiers' sharding plugin |

The `fsla_devtools` Bazel module (`MODULE.bazel`, `bazel/`) is the rest of the build graph's machinery: its
environment extension and generic rules (below).

## The declarations and the tripwire

Every FSLA repository is developed in public, with its whole history. `.github/mirror/declarations.json` in a repository
declares every tracked path (`ships` and `private` list path prefixes; a path takes the longest entry it falls under),
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
reads HEAD whole. A refusal is a line `TRIPWIRE REFUSED: path:line: the check`. Files declared `private` are not read.

```bash
python -m fsla_devtools.mirror --check       # every tracked path declared
```

## The declared build system

A DECLARATION FILE is plain Python, loaded by path, whose functions declare TARGETS on a graph. A target is an executor
(`module:function`) that runs in an environment (`Env`: a python, a directory, variables; none is the build system's
own), the targets it reads by role, the central cells it takes, its parameters, the resources it holds while it runs,
whether it caches, whether it runs after a failure (`always_run`), a function that confirms a cached result still holds
on this machine (`verify`), and the code its result depends on. A group is a target with no executor. Labels are scoped
(`g.scoped("tip")`), so one function declares one checkout's targets under any label, and a root composes declaration
files from several checkouts:

```python
def root(g, target):
    fsla = load(Path(target) / "declare.py")
    tip = fsla["declare"](g.scoped("tip"), fsla["checkout"](target, python=..., label="tip"), cells=["flagship-dense"])
    return {"all": g.group("all", [tip["binary"], *tip["instruments"].values()])}
```

A target's KEY is sha256 over its executor, parameters, declared code digest, and each dependency's key and output
digest -- a DATA INPUT is a dependency too, so a cell node's record and the digest of the code that drew it reach the key
that way; labels, paths and run ids never enter it. The
scheduler takes the targets in dependency order: a cached target whose key the build cache holds (`host.build_cache`,
wipeable) is skipped, the rest run with their resources held -- `{"gpu": "all"}`, `{"gpu": 1}`, `{"host_cpu": 8}`,
`{"clock": 1}`, counting semaphores over the machine's own locks, so processes outside the build are excluded too. An
exception is a BUILD failure: the build stops scheduling and only `always_run` targets still run. What a target records
as failed inside its own output (a timing entry whose kernel is not built) is its domain's, and the build goes on.

```bash
python -m fsla_devtools.build plan declare.py:all
python -m fsla_devtools.build run  declare.py:all --only 'fsla/phases' --skip '*/timeline'   # prune by label
```

## The timing system

Timing is a client of the build system (`fsla_devtools.timing`). `start_timing_server` starts a pool of entry workers,
one per checkout environment; `register_timing` is one target registering a checkout's timed callable on every cell it
takes, as ENTRIES; `measure_timing` is a session over the entries of any registrations: every entry set up behind a
barrier, warmed past a floor of 10 calls, then called one at a time in a fresh random order each rep, with the entry's
untimed reset before every call, under the GPU and clock locks with the clock read through a registered reader before
and after. The session keeps every sample in the order taken, with its round, rep and position; which entry is the
reference is chosen when the samples are read. An entry that cannot set up is recorded as that member's failure; two
stopwatches or a clock off the lock fail the build. `measure_memory` takes each entry alone, and `measure_null_gate`
times one registration's entries against copies of themselves in second workers, finding a worker's bias.
`fsla_devtools.store.store` writes a target's result through the store (`fsla_devtools.results`) as a run-stamped sample
of the record its source's semantics key.

## The diff

"The same function in two checkouts" and "the kernel against its fp64 reference" are the same question asked twice, so
`fsla_devtools.diff` asks it once. `side` is one side of a comparison: an executor run in the environment it names over
every cell TARGET it takes, writing its tensors into its own node's workspace. `diff` takes two sides and one named
STRATEGY -- `bit-identical` (a change that claims to have moved no number), `per-slot` (the oracle's clause rule: a slot
of size `s` may be off by `max(r*s, a)` under each `(r, a)` clause of its output kind, and by `rtol * envelope` where
the output is multilinear, the smallest term binding), `support-equal` (which slots survive is the claim, then their
values) -- and states what it EXPECTS: `same` is a gate on an invariant, `different` is the non-vacuity half, where a
mutant that goes unseen is the failure. `on_difference` picks what a broken claim is, the same two-way choice every
other target makes: a build failure, or the target's own recorded outcome.

Two lessons from the tool this replaces are built into the node rather than left to arrangement. A side NAMES the
library it is supposed to be running (`binds`) and proves it: an editable install resolves its package through the
install's path entry -- the canonical checkout -- so a side run from another worktree can import the very tree it is
being compared against and agree with it perfectly, having compared nothing (measured, not hypothetical). The side
refuses when its library resolved outside its own directory, and the comparison refuses two sides that resolved the
same file. And a diff states the COUNT it needs (`minimum`): a comparison that compared less than it declared is a
refusal, not a pass, because a gate that cannot run says so instead of reporting a verdict over whatever survived.

The raw never leaves the workspace. A side's tensors live in the build cache, which is wipeable and swept; what the
diff node outputs, and what a store target beside it would file, is the DIFF -- per cell and quantity the worst slot,
where it is, what bound it, and how many slots were compared. A cell either side could not produce is not a comparison
that passed: it is counted as unusable and the claim does not hold, because a gate that goes green on a cell neither
side ran measured nothing.

## The results store

Every measuring tool stores what it measured through one library, `fsla_devtools.results`; the records themselves are
the data repository's (fsla-results, a repository of data and no code), at the checkout the dev config's `store.root`
names.

```python
from fsla_devtools.results import Store, checkout

store = Store("mqar/local")                        # the tool's own location, under store.root's records/
semantics = {"bench": "mqar", "config": "local", "cell": "d1-n256", "local": checkouts}
store.put(semantics, output=rows, wall_s=12.3, provenance=checkout(worktree))
store.put(semantics, error="RuntimeError: ...")    # a failure is a sample too
record = store.get(key(semantics))                  # Store.complete(record), store.output(record)
```

- **A location** names who stores: `fsla/sass`, `timing/session`, `mqar/<config>`.
- **A record** is everything kept under one key: the semantics the tool handed over and every sample.
- **The key** is sha256 over the semantics, so a repeat lands on the same record and a changed input on a new one.
- **A sample** is one execution: its output (JSON, or a file moved in) or its error, when, how long, its provenance
  (the checkouts and commits the tool names; the host).

    records/<location>/<key>.json            the record (format 1: key, location, format, semantics, samples)
    records/<location>/<key>.<n>.out.<ext>   sample n's output

Nothing derived from two records (a ratio, a baseline) is stored: that is the reader's. **A record carries no machine
path**: a path inside a home directory in anything stored is refused at `put` -- a person's, a container's or a rented
box's alike -- since a record is read on other machines and published; a file is named by its path inside its checkout
(`portable(text, *checkouts)` rewrites a message) and a checkout by `checkout()`, its directory's name, branch, HEAD and
the digest of its uncommitted diff. A git call that fails is a failure, never "clean, no commit". **A timing names its
commits**: a sample at a `timing/...` location whose provenance's `checkouts` does not give every checkout's `git_sha` is
refused. A MACHINE PATH is a path inside someone's home directory (`/home/<user>`, `/Users/<user>`, `/root`, a Windows user
folder); a system path (`/opt/venv/bin/python`) is a fact of the environment and is kept. The rule is the writer's, refused
where a sample is written: `check(root)` recomputes every key, holds every location to its directory and every successful
sample to its output, and reads no record's content, since a stored record is never rewritten; `check_files(directory)`
holds a directory of files kept as written (the data repository's `lab/`) to parsing.

**The index** (`fsla_devtools.results.index`) is the records as one SQLite database, derived: `records(location, key,
format, semantics)` and `samples(location, key, n, ok, utc, wall_s, provenance, error, output_path, output, extra)`, the
JSON fields read with SQLite's JSON functions, and a tool's shape a view in `fsla_devtools/results/views/`, created after
every index (a view that left the package leaves the database). A query's database is a cache of the machine
(`results/<12 hex of the records path>/index.sqlite` under `host.build_cache`), rebuilt incrementally; the data
repository's own index is a build output (`results_index`, `bazel/results.bzl`), never a file beside the records.

| view | from | one row per |
|---|---|---|
| `timing_members` | `timing/session` (`measure_timing`, through a store target) | member of a stored session: the run, the session, its registration (`owner`), the label and arm read from it, the cell, the LEVEL it prices (`kernel`, `op`, `layer`), whether it ran and why not, device, torch, the owner's commit and diff, the session's rounds, reps and clock reads |
| `timing_samples` | `timing_members` | timed call, in the order taken: its member's label, arm and cell, its round, rep and position in the rep's random order, and its milliseconds |
| `memory_rows` | `timing/memory` (`measure_memory`) | timing entry alone on a central cell: peak allocated and reserved bytes, what was allocated before the build and after the calls, bytes held outside the caching allocator and the totals with them, label, arm, commit |
| `instrument_cells` | `fsla/<instrument>` (a fsla checkout's instrument targets) | cell of an instrument run: the run, whether the tool ran on it, and its exit and error when it could not |
| `instrument_metrics` | `fsla/<instrument>` | every NUMBER a declared instrument recorded, one row each, in long form: the run, the cell (NULL for the whole-binary `sass`), a dotted `metric` (`sass.<cubin>.<function>.<count>`) and its value |
| `diff_cells` | `fsla/<name>` and `diff/<surface>` (the diff targets) | cell and quantity of a stored diff: the claim and whether it `held`, the cell's status (`ok`, `unusable`, `refused`) and why, and the quantity's verdict with why it differs and its worst slot |
| `null_gates` | `timing/null` (`measure_null_gate`) | cell of a null gate: the run, whether the entry's two workers agree, the ratio's median and quartiles |
| `cells` | fleet and local grids | stored grid cell: bench, config, cell, image or local checkouts, row |

```bash
python -m fsla_devtools.results [--root RECORDS] check   # the default root: store.root's records/
python -m fsla_devtools.results check-files DIR
python -m fsla_devtools.results show [LOCATION]
python -m fsla_devtools.results index [--db FILE]
python -m fsla_devtools.results sql "SELECT ..."
python -m fsla_devtools.results history|latest LOCATION [--where PATH=VALUE]
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

A cell is an input, named once for every package: `fsla_devtools/cells/carry.json` (a sparse recurrence's routing
amplitudes, gain, values and the state it enters with, including the state's backing), `layer.json` (a layer's hidden
states and values) and `qkv.json` (attention's queries, keys and values). A record holds the input and never how a
package runs it -- a kernel's launch shape, FSLA's routing widths or gain, an attention backend are an arm's -- so two
arms on one cell read the same tensors, and when the registry changes, a package that no longer fits it is what changes.

```python
from fsla_devtools.cells import cell, carry

drawn = carry.realize(cell("flagship-alt-k4"))   # bf16 read/write levels, gain, v; the entry state of a carried cell
```

Every carry cell but the two degenerate ones declares where in the routing distribution it sits on six axes (density,
coherence, read/write correlation, mass, tail, support; `fsla_devtools.cells.regimes`), and `realize` proves the draw is
there before returning it. Every cell states the seed its data is drawn from, so a failure reproduces from the record.
A base (`bases.json`) is a fragment, never a cell; a derived cell merges the bases its `from` lists in order, and `vary`
with a name pattern makes one cell per value. Loading and checking the registry needs only the standard library;
realizing a cell needs torch.

## The dev config and the machine's locks

Every value that depends on the machine lives in one directory of JSON files, one a section: `~/.config/fsla/`, or the
directory `FSLA_DEV_CONFIG` names (a container, a test). A schema declares each section's keys with a kind, a default and
a meaning; a key a file holds that its section does not declare is refused, so a typo fails instead of silently taking
a default. `fsla_devtools.config.MACHINE` declares the sections every repository shares -- `host` (lock paths, the
compute budget, niceness), `clock` (the SM clock the harness locks) and `store` (`root`, the data repository's checkout
the results store writes) -- and `machine("host.gpu_lock")` reads one value.
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
acquire in the same process or a child is a no-op, so an outer tool that locks can start an inner tool that locks.
`fsla_devtools.locks.clock.engage(read_ghz)` locks the clock the host declares, proves it with the device's own reading,
and unlocks on every exit.

## The build graph

Every FSLA repository builds and checks itself with Bazel, through the same machinery: this repository's Bazel module.
A repository depends on it at a public commit (`bazel_dep` plus a `git_override` in its `MODULE.bazel`, the one pin of
fsla-devtools it has).

**The front door.** bazelisk runs a repository's `bazel/bazel` (the wrapper its `.bazeliskrc` names) in place of the
Bazel it downloaded; that stub runs `fsla_devtools.graph.front_door`, found through the machine's fsla-devtools checkout,
so every Bazel command in a checkout goes through one implementation. It hands Bazel the machine's settings from the
dev config (the shared caches and the environment's store), overrides the repository's pin of this module with the
machine's checkout (an rc file of the workspace's own), so a change made here is the one every graph on the machine
runs, and holds the host budget around each build phase. `bazel --commit-gate` is the commit gate below.

**The environment extension** (`bazel/environment.bzl`). Its tags make the repositories a graph runs on:

| Tag | Repository |
|---|---|
| `python` | a Python environment: the pinned CPython, a hash-pinned lock's packages, then packages pinned by commit and this module's `fsla_devtools`, built once a key into the machine's shared store (`fsla_devtools.graph.make_env`); `isolated_builds = False` builds the pinned packages against the environment, for one whose build imports a locked package |
| `pinned` | a release pinned by URL and digest (uv, mold, clang-tidy) |
| `cuda_toolkit` | a CUDA toolkit from NVIDIA's redistributables, pinned by their manifest's digest (which carries each component's) |
| `host_cc`, `host_gpu` | the host C++ compiler's and the machine's devices' identities, which actions that depend on them are keyed on |
| `tree` | the checkout's tracked files (`git ls-files`; the commit's index under the commit gate) |
| `data` | the data repository at one commit (`remote`, `commit`, the `sha256` of the commit's archive): its `records/` and `lab/`, as `:records`, `:lab`, each file by its path, and `:root` (its BUILD file, which names the root) (`bazel/data.BUILD`), nothing of its own graph -- what a workspace's citations and readers take, fixed content at the pin |

`python`, `pinned`, `cuda_toolkit` and `data` are REPRODUCIBLE repositories (`repo_metadata(reproducible = True)`: their result
is fixed by their pins and the files they read), so Bazel unpacks each once into the repo contents cache
(`host.build_cache`'s `repository/contents`) and every checkout's output base links it; `host_cc`, `host_gpu` and `tree`
read the machine or the workspace and are made per checkout.

**The environment store is bounded.** `host.store_max_gb` and `host.store_max_age_days` (the dev config) bound the store
the `python` tag builds into: each environment build sweeps, under the store's lock, the environments idle past the age
and the cached wheels nothing hardlinks, and, over the size, the oldest of them, never the environment it was asked for;
a swept environment a checkout still names is rebuilt from the cache or the index.

`pins/uv_pin.json` is the uv every workspace's environment is built by.

**The generic rules** (`bazel/rules.bzl`): a check over the tracked tree (`tree_test`), a check of the commit
(`checkout_test`), a tree tool as a test (`tool_test`) or a run target (`tool_run`), a package's part of the tree
(`tree_part`), and the pytest tiers (`pytest_tier`, `device_lane`). Each takes the workspace's environment as an
argument; a workspace wraps them once with its own. A check over the tree or a tier that reads another repository's
files names it in `repos` (a label of it -> a variable): its files join the runfiles and its root is exported as that
variable (`{"@fsla_data//:lab": "FSLA_DATA"}`, the pinned data). **The results store's rules** (`bazel/results.bzl`): the records'
index as a build output (`results_index`), a data repository's.

## Tests and the gate

```bash
bazel test //:gate
```

The gate is ruff, its blank-line family, the tests and the declaration check, on the graph's own environment.
The commit gate (`.githooks/pre-commit` -> `bazel/bazel --commit-gate`, every FSLA repository's hook) runs `//:gate` on
the commit as git will write it: the changes not staged set aside, the tree the commit's index.

## Publishing

This repository is developed in public: `Blakexx/fsla-devtools` is the development repository, with its whole history.

## License

Apache-2.0.
