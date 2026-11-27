"""THE MEASUREMENT RULES, every FSLA repository's: a cell's description (`cell`, which the environment extension's `cells`
tag declares one of a central cell), a timed callable on cells with the bundle of code it runs in (`timing_entry`), an
interleaved session over entries that `bazel run` starts and stores (`interleaved_timing`), a tool's JSON over the files
it reads (`instrument`), and the record of a build output that `bazel run` stores (`results_store`). Docs:
README.md#timing, README.md#instruments.

    timing_entry(name = "entmax_solve_<cell>", executor = "measure.executors:timed", params = {"arm": ...},
                 cells = ["@fsla_cells//:<cell>"], data = [...], binds = ["fsla_cu13._C"], venv = VENV, python = ...)
    interleaved_timing(name = "session_entmax", entries = [":entmax_solve_<cell>"], clock = ":clock")
    instrument(name = "oracle_tier", tool = "oracle_tier.py", data = [...], devices = "@host_gpu//:identity.txt", venv = VENV)
    results_store(name = "store_oracle_tier", src = ":oracle_tier", location = "fsla/oracle-tier", venv = VENV, python = ...)
    results_store(name = "store_static_manifest", src = "//:static_manifest", inputs = ["//:library", ...], ...)

An instrument is a tool run over the files it reads, laid out as a tree (`fsla_devtools.instrument`): its JSON, or
`{"refused": WHY}` when it cannot run, which stops nothing else. Without a device it is an action, its JSON a build
output; on the device it runs in the run phase of the store target that records it, since a build holds the host budget
and the GPU taken under it deadlocks against a measurement, which takes the GPU first. A store target's record keys each
output file by its source, its name and the content digest of every input it depends on, stamps the workspace's commit
into its provenance, and its program (`fsla_devtools.record`) asks the store whether the record already holds the outcome
before it writes anything.

Everything a session needs is a build output: each cell's description (its record and the digest of the code that draws
it, never its tensors), each entry's description (written here, nothing imported) and BUNDLE -- its runfiles (the code it
imports, its library, the files laid out where that code reads them) and `<name>.bundle.json` (the workspace's stamped
commit, the bundle's content digest, its environment) -- and the session's manifest. The interleaving itself, the locks
and the store are the session program's (`fsla_devtools.timing.session`), which the run target's launcher starts; nothing
is timed by an action and nothing outlives the run.

An entry is an executor (`module:function`) that, called with a cell's data and the entry's `params` in a worker running
on the bundle's interpreter with the bundle's runfiles as its whole import path, returns a `fsla_devtools.timing.Timed`:
set up, call, reset. A second form of an entry -- the same call captured once and replayed -- is another executor over
the same bundle and cells, declared as its own target, so its label (the member's owner in the record) is never its eager
twin's.
"""

load("@bazel_skylib//lib:shell.bzl", "shell")

CellInfo = provider(
    doc = "A cell's description: its record (provider, parameters, seed) and the digest of the code that draws it.",
    fields = {"name": "the cell's name", "description": "the description's JSON file"},
)

TimingEntryInfo = provider(
    doc = "A timing entry: its description and the runfiles of its bundle.",
    fields = {
        "owner": "the entry's label, the owner of its members in a session's record",
        "kind": "`timed`, or `clock` for the in-kernel clock read that proves a session's lock",
        "description": "the entry's JSON description",
        "runfiles": "everything a worker for the entry reads: its bundle, its bundle manifest, its cells' descriptions",
    },
)

#: THE WORKSPACE'S STAMP AND A CONTENT DIGEST, the start of each action below that writes one (run on the environment's
#: base interpreter, standard library only): the STAMP is the commit, branch, checkout name and the digest of its
#: uncommitted tracked diff, which the status command writes as the `STABLE_` keys below (`--workspace_status_command`;
#: Bazel fails the build when it fails), and a workspace that stamps no commit is refused: a number names the code it
#: measured. A CONTENT DIGEST is sha256 over files, each by its name and its bytes' digest.
_STAMP = """\
import argparse, hashlib, json, pathlib
KEYS = {"STABLE_GIT_COMMIT": "git_sha", "STABLE_GIT_BRANCH": "branch", "STABLE_GIT_DIFF_SHA256": "diff_sha256",
        "STABLE_CHECKOUT": "checkout"}
def stamp(status):
    keys = dict((line + " ").split(" ", 1) for line in status.read_text().splitlines() if line)
    keys = {k: v.strip() for k, v in keys.items()}
    missing = sorted(set(KEYS) - set(keys))
    if missing or not keys["STABLE_GIT_COMMIT"]:
        raise SystemExit("the workspace stamps no commit (%s): its .bazelrc names no --workspace_status_command writing %s"
                         % (missing or "STABLE_GIT_COMMIT is empty", sorted(KEYS)))
    return {field: keys[key] or None for key, field in KEYS.items()}
def digest(files):
    h = hashlib.sha256()
    for name, path in sorted(files):
        h.update(name.encode() + b"\\0" + hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest().encode() + b"\\0")
    return h.hexdigest()
"""

#: THE BUNDLE MANIFEST'S ACTION: the workspace's stamp, the bundle's CONTENT DIGEST (every file by its path in the bundle)
#: and its ENVIRONMENT (the store venv's key, its path's last part)
_BUNDLE = _STAMP + """\
ap = argparse.ArgumentParser(fromfile_prefix_chars="@")
ap.add_argument("--status", type=pathlib.Path)
ap.add_argument("--venv")
ap.add_argument("--out", type=pathlib.Path)
ap.add_argument("--file", nargs=2, action="append", default=[])
a = ap.parse_args()
a.out.write_text(json.dumps({"stamp": stamp(a.status), "digest": digest(a.file), "files": len(a.file),
                             "environment": pathlib.PurePath(a.venv).name}, indent=1, sort_keys=True) + "\\n")
"""

def _rlocation(ctx, f):
    """A file's path in a run target's runfiles."""
    return f.short_path[3:] if f.short_path.startswith("../") else ctx.workspace_name + "/" + f.short_path

def _repository(canonical):
    """A repository by its own name, never the canonical name Bazel derives for it (`warpline+`,
    `fsla_devtools++environment+host_gpu`), which moves with Bazel's release and the module graph."""
    return canonical.replace("~", "+").rstrip("+").split("+")[-1]

def _owner(label):
    """A label as a record names it, its repository by `_repository`."""
    repo = _repository(label.workspace_name)
    return ("@%s" % repo if repo else "") + "//%s:%s" % (label.package, label.name)

def _cell_impl(ctx):
    return [DefaultInfo(files = depset([ctx.file.src])), CellInfo(name = ctx.label.name, description = ctx.file.src)]

cell = rule(
    implementation = _cell_impl,
    attrs = {"src": attr.label(mandatory = True, allow_single_file = [".json"], doc = "the cell's description")},
    doc = "A cell as a build output: its description, a few hundred bytes, never its tensors.",
)

def _timing_entry_impl(ctx):
    kind = ctx.attr.kind
    if (kind == "clock") == bool(ctx.attr.cells):
        fail("%s: a timed entry takes cells and a clock reader none" % ctx.label)
    laid_out = {}
    for target, path in ctx.attr.layout.items():
        found = target[DefaultInfo].files.to_list()
        if len(found) != 1:
            fail("%s: %s names %d files, not one" % (ctx.label, target.label, len(found)))
        laid_out[path] = found[0]
    bundle_runfiles = ctx.runfiles(files = ctx.files.data, symlinks = laid_out).merge_all(
        [d[DefaultInfo].default_runfiles for d in ctx.attr.data],
    )
    root = ctx.label.workspace_name or ctx.workspace_name

    #: THE BUNDLE'S MANIFEST: the workspace's stamp (a failed status command fails the build) and the content digest
    bundle = ctx.actions.declare_file(ctx.label.name + ".bundle.json")
    files = ctx.actions.args()
    files.use_param_file("@%s", use_always = True)
    files.set_param_file_format("multiline")
    for f in bundle_runfiles.files.to_list():
        files.add_all(["--file", _rlocation(ctx, f).removeprefix(root + "/"), f])
    for path, f in laid_out.items():
        files.add_all(["--file", path, f])
    args = ctx.actions.args()
    args.add_all(["-I", "-c", _BUNDLE])
    args.add("--status", ctx.info_file)
    args.add("--venv", ctx.attr.venv)
    args.add("--out", bundle)
    ctx.actions.run(
        executable = ctx.file.python,
        arguments = [args, files],
        inputs = depset([ctx.info_file] + laid_out.values(), transitive = [bundle_runfiles.files]),
        outputs = [bundle],
        mnemonic = "TimingBundle",
        progress_message = "Stamping and digesting %{label}'s bundle",
    )

    cells = [c[CellInfo].description for c in ctx.attr.cells]
    description = ctx.actions.declare_file(ctx.label.name + ".entry.json")
    ctx.actions.write(description, json.encode_indent({
        "owner": _owner(ctx.label),
        "kind": kind,
        "executor": ctx.attr.executor,
        "params": json.decode(ctx.attr.params),
        "binds": ctx.attr.binds,
        "cells": [_rlocation(ctx, c) for c in cells],
        "bundle": {"root": root, "manifest": _rlocation(ctx, bundle), "python": ctx.attr.venv + "/bin/python3"},
    }) + "\n")
    runfiles = bundle_runfiles.merge(ctx.runfiles(files = [description, bundle] + cells))
    return [
        DefaultInfo(files = depset([description, bundle]), runfiles = runfiles),
        TimingEntryInfo(owner = _owner(ctx.label), kind = kind, description = description, runfiles = runfiles),
    ]

_timing_entry = rule(
    implementation = _timing_entry_impl,
    attrs = {
        "executor": attr.string(mandatory = True, doc = "`module:function`: called with a cell's data and `params` in the " +
                                                        "entry's worker, it returns a `Timed` (a clock reader: the GHz)"),
        "params": attr.string(default = "{}", doc = "the executor's parameters, as JSON"),
        "kind": attr.string(default = "timed", values = ["timed", "clock"]),
        "cells": attr.label_list(providers = [CellInfo], doc = "the cells it is timed on, a session member each"),
        "data": attr.label_list(allow_files = True, doc = "the bundle: the code the executor imports and what it reads"),
        "layout": attr.label_keyed_string_dict(allow_files = True, doc = "a file laid out at a path of the bundle, where " +
                                                                         "its code reads it"),
        "binds": attr.string_list(doc = "modules a worker must load from inside the bundle, each checked after set-up"),
        "venv": attr.string(mandatory = True, doc = "the environment's store venv, whose interpreter the worker runs on"),
        "python": attr.label(mandatory = True, allow_single_file = True, cfg = "exec", doc = "the environment's base " +
                                                                                             "interpreter, for the manifest"),
    },
    doc = "One timed callable on cells, with the bundle of code it runs in: its description and its bundle manifest.",
)

def timing_entry(name, params = {}, **kwargs):
    """A timing entry; `params` a dict, written as JSON."""
    _timing_entry(name = name, params = json.encode(params), **kwargs)

_SESSION = """\
#!/bin/bash
# a timing session (fsla_devtools' interleaved_timing): the session program on the checkout's own venv (`fsla.venv`),
# which imports the store; each entry's worker runs on its bundle's interpreter, its bundle its whole import path
set -euo pipefail
checkout="${{BUILD_WORKSPACE_DIRECTORY:?run through bazel run}}"
venv="$(git -C "$checkout" config --get fsla.venv || true)"
if [ ! -x "$venv/bin/python" ]; then
  echo "$checkout has no venv of its own (git config fsla.venv)" >&2
  exit 1
fi
runfiles="${{RUNFILES_DIR:-$(realpath -s "$0").runfiles}}"
exec "$venv/bin/python" -P -m fsla_devtools.timing.session --runfiles "$runfiles" --manifest {manifest} "$@"
"""

def _interleaved_timing_impl(ctx):
    for entry in ctx.attr.entries:
        if entry[TimingEntryInfo].kind != "timed":
            fail("%s: %s is a clock reader, not a timed entry" % (ctx.label, entry.label))
    if ctx.attr.clock and ctx.attr.clock[TimingEntryInfo].kind != "clock":
        fail("%s: %s is not a clock reader" % (ctx.label, ctx.attr.clock.label))
    members = ctx.attr.entries + ([ctx.attr.clock] if ctx.attr.clock else [])
    manifest = ctx.actions.declare_file(ctx.label.name + ".session.json")
    ctx.actions.write(manifest, json.encode_indent({
        "session": _owner(ctx.label),
        "claim": ctx.attr.claim,
        "rounds": ctx.attr.rounds,
        "reps": ctx.attr.reps,
        "warmup": ctx.attr.warmup,
        "location": ctx.attr.location,
        "entries": [_rlocation(ctx, e[TimingEntryInfo].description) for e in ctx.attr.entries],
        "clock": _rlocation(ctx, ctx.attr.clock[TimingEntryInfo].description) if ctx.attr.clock else None,
    }) + "\n")
    launcher = ctx.actions.declare_file(ctx.label.name + ".sh")
    ctx.actions.write(launcher, _SESSION.format(manifest = shell.quote(_rlocation(ctx, manifest))), is_executable = True)
    runfiles = ctx.runfiles(files = [manifest]).merge_all([m[TimingEntryInfo].runfiles for m in members])
    return [DefaultInfo(executable = launcher, files = depset([manifest]), runfiles = runfiles)]

interleaved_timing = rule(
    implementation = _interleaved_timing_impl,
    executable = True,
    attrs = {
        "entries": attr.label_list(mandatory = True, providers = [TimingEntryInfo], doc = "the timed entries"),
        "clock": attr.label(providers = [TimingEntryInfo], doc = "the clock reader that proves the host's lock"),
        "rounds": attr.int(default = 8),
        "reps": attr.int(default = 11, doc = "odd, so a round's median is a sample"),
        "warmup": attr.int(default = 10, doc = "calls of each member discarded first; at least the program's floor"),
        "location": attr.string(default = "timing/session", doc = "the store's location the session is kept at"),
        "claim": attr.string(doc = "what the session claims of itself; `unlocked` admits a host with no clock lock"),
    },
    doc = "An interleaved timing session over entries: `bazel run` measures and stores it (`-- --no-store`, " +
          "`--scratch DIR`, `--store-root DIR`).",
)

InstrumentInfo = provider(
    doc = "An instrument: the tool, its arguments and the files it reads, and its output where a build makes it.",
    fields = {
        "tool": "the tool's path in the instrument's tree",
        "args": "its arguments, before `--json OUT`",
        "files": "every file it reads, laid out in its tree",
        "inputs": "what its output depends on: (label, its files) for the tool, each of `data` and the devices' identity",
        "output": "the JSON a build makes; None for an instrument on the device, which its store target runs",
        "name": "the output's name",
    },
)

def _tree_path(f):
    """A file's path in an instrument's tree and in a record: the main repository's at its path, another repository's
    under `external/<its own name>/` (`_repository`)."""
    if not f.short_path.startswith("../"):
        return f.short_path
    repo, _, path = f.short_path[3:].partition("/")
    return "external/%s/%s" % (_repository(repo), path)

def _tree_entry(f):
    return [_tree_path(f), f.path]

def _instrument_impl(ctx):
    targets = [ctx.attr.tool] + ctx.attr.data
    args = [ctx.expand_location(a, targets) for a in ctx.attr.arguments]
    files = depset([ctx.file.tool] + ctx.files.data)
    inputs = [(_owner(t.label), t.files) for t in targets]
    if ctx.attr.devices:
        inputs.append((_owner(ctx.attr.devices.label), ctx.attr.devices.files))
    name = ctx.label.name + ".json"
    if ctx.attr.devices:
        #: named as the action's output would be, by its path in the tree
        repo = _repository(ctx.label.workspace_name)
        where = (["external", repo] if repo else []) + [ctx.label.package, name]
        return [DefaultInfo(), InstrumentInfo(tool = _tree_path(ctx.file.tool), args = args, files = files, inputs = inputs,
                                              output = None, name = "/".join([w for w in where if w]))]

    #: an instrument without a device is an action: its tree laid out, the tool run in it, its JSON the output
    listing = ctx.actions.args()
    listing.set_param_file_format("multiline")
    listing.add_all(files, map_each = _tree_entry)
    files_list = ctx.actions.declare_file(ctx.label.name + ".files")
    ctx.actions.write(files_list, listing)
    output = ctx.actions.declare_file(name)
    run = ctx.actions.args()
    run.add_all(["-I", "-m", "fsla_devtools.instrument", "--files", files_list, "--tool", _tree_path(ctx.file.tool)])
    run.add("--out", output)
    run.add("--")
    run.add_all(args)
    ctx.actions.run(
        executable = ctx.attr.venv + "/bin/python3",
        arguments = [run],
        inputs = depset([files_list], transitive = [files]),
        outputs = [output],
        mnemonic = "Instrument",
        progress_message = "Running %{label}'s tool",
        #: the environment's interpreter reads its packages from the store, which the sandbox does not hold; its store
        #: path on the command line keys the action by the environment
        execution_requirements = {"no-sandbox": "1"},
    )
    return [DefaultInfo(files = depset([output])), InstrumentInfo(tool = _tree_path(ctx.file.tool), args = args,
                                                                  files = files, inputs = inputs, output = output,
                                                                  name = _tree_path(output))]

instrument = rule(
    implementation = _instrument_impl,
    attrs = {
        "tool": attr.label(mandatory = True, allow_single_file = [".py"], doc = "the tool, run as `TOOL ARGS... --json " +
                                                                               "OUT` on the environment's interpreter"),
        "arguments": attr.string_list(doc = "its arguments, `$(rootpath ...)` of `tool` and `data` expanded to the tree's " +
                                            "paths"),
        "data": attr.label_list(allow_files = True, doc = "the files it reads, laid out at their paths in its tree"),
        "devices": attr.label(allow_single_file = True, doc = "an instrument on the device: the devices' identity, an " +
                                                              "input of its record; its store target runs it"),
        "venv": attr.string(mandatory = True, doc = "the environment's store venv, whose interpreter runs the tool"),
    },
    doc = "A tool's JSON over the files it reads: a build output, or, on the device, what its store target runs. A tool " +
          "that cannot run writes `{\"refused\": WHY}` and exits 0; one that fails fails.",
)

#: THE RECORD'S ACTION: the skeleton Starlark wrote (the source, the location, each output file, an instrument's run),
#: with the workspace's stamp and each input's CONTENT DIGEST (every file of it by its path in the tree)
_RECORD = _STAMP + """\
ap = argparse.ArgumentParser(fromfile_prefix_chars="@")
ap.add_argument("--skeleton", type=pathlib.Path)
ap.add_argument("--status", type=pathlib.Path)
ap.add_argument("--out", type=pathlib.Path)
ap.add_argument("--inputs", type=pathlib.Path)
a = ap.parse_args()
inputs, label = {}, None
lines = iter(a.inputs.read_text().splitlines())
for line in lines:
    if line == "--label":
        label = next(lines)
        inputs[label] = []
    else:
        inputs[label].append((line, next(lines)))
record = json.loads(a.skeleton.read_text())
record.update(stamp=stamp(a.status), inputs={label: digest(files) for label, files in inputs.items()})
a.out.write_text(json.dumps(record, indent=1, sort_keys=True) + "\\n")
"""

_RECORD_RUN = """\
#!/bin/bash
# a build output's record (fsla_devtools' results_store): fsla_devtools.record on the environment's interpreter, which
# stores each output file unless its record already holds it (an instrument on the device runs first)
set -euo pipefail
runfiles="${{RUNFILES_DIR:-$(realpath -s "$0").runfiles}}"
exec {python} -I -m fsla_devtools.record --runfiles "$runfiles" --record {record} "$@"
"""

def _results_store_impl(ctx):
    src = ctx.attr.src
    instrument = src[InstrumentInfo] if InstrumentInfo in src else None
    if instrument == None and not ctx.attr.inputs:
        fail("%s: %s is no instrument, so `inputs` names what its output depends on" % (ctx.label, src.label))
    inputs = [(_owner(t.label), t.files) for t in ctx.attr.inputs] + (instrument.inputs if instrument else [])
    device = instrument != None and instrument.output == None
    if device:
        outputs = [{"name": instrument.name, "path": None}]
        carried = instrument.files
    else:
        found = src[DefaultInfo].files.to_list()
        if not found:
            fail("%s: %s builds no file" % (ctx.label, src.label))
        outputs = [{"name": _tree_path(f), "path": _rlocation(ctx, f)} for f in found]
        carried = depset(found)
    skeleton = ctx.actions.declare_file(ctx.label.name + ".skeleton.json")
    ctx.actions.write(skeleton, json.encode_indent({
        "source": _owner(src.label),
        "location": ctx.attr.location,
        "outputs": outputs,
        "instrument": None if instrument == None else {
            "tool": instrument.tool,
            "args": instrument.args,
            "device": device,
            "files": [[_tree_path(f), _rlocation(ctx, f)] for f in instrument.files.to_list()] if device else [],
        },
    }) + "\n")
    listing = ctx.actions.args()
    listing.set_param_file_format("multiline")
    for label, files in inputs:
        listing.add("--label", label)
        listing.add_all(files, map_each = _tree_entry)
    inputs_list = ctx.actions.declare_file(ctx.label.name + ".inputs")
    ctx.actions.write(inputs_list, listing)
    record = ctx.actions.declare_file(ctx.label.name + ".record.json")
    args = ctx.actions.args()
    args.add_all(["-I", "-c", _RECORD])
    args.add("--skeleton", skeleton)
    args.add("--status", ctx.info_file)
    args.add("--inputs", inputs_list)
    args.add("--out", record)
    ctx.actions.run(
        executable = ctx.file.python,
        arguments = [args],
        inputs = depset([skeleton, inputs_list, ctx.info_file], transitive = [files for _label, files in inputs]),
        outputs = [record],
        mnemonic = "ResultsRecord",
        progress_message = "Stamping and digesting %{label}'s record",
    )
    launcher = ctx.actions.declare_file(ctx.label.name + ".sh")
    ctx.actions.write(launcher, _RECORD_RUN.format(python = shell.quote(ctx.attr.venv + "/bin/python3"),
                                                   record = shell.quote(_rlocation(ctx, record))), is_executable = True)
    return [DefaultInfo(executable = launcher, files = depset([record]),
                        runfiles = ctx.runfiles(files = [record], transitive_files = carried))]

results_store = rule(
    implementation = _results_store_impl,
    executable = True,
    attrs = {
        "src": attr.label(mandatory = True, doc = "the build output stored: each of its files a record; an instrument"),
        "location": attr.string(mandatory = True, doc = "the store's location the records are kept at"),
        "inputs": attr.label_list(allow_files = True, doc = "what the output depends on, beyond an instrument's own: " +
                                                            "their files' digests key the record"),
        "venv": attr.string(mandatory = True, doc = "the environment's store venv, whose interpreter runs the store"),
        "python": attr.label(mandatory = True, allow_single_file = True, cfg = "exec", doc = "the environment's base " +
                                                                                             "interpreter, for the record"),
    },
    doc = "The record of a build output: `bazel run` stores each of its files unless the store already holds it " +
          "(`-- --store-root DIR`, `--no-store`, `--scratch DIR`).",
)
