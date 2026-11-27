"""THE MEASUREMENT RULES, every FSLA repository's: a cell's description (`cell`, which the environment extension's `cells`
tag declares one of a central cell), the code a tree's entries run in (`timing_bundle`), a timed callable, the clock
reader or a diff side on cells in a bundle (`timing_entry`, `diff_side`), one of those built in ANOTHER workspace by its
own commit's pins (`exported_entry`, which the extension's `bundle` tag generates), an interleaved session over entries
that `bazel run` starts and stores, or their null gate (`interleaved_timing`), the entries' memory pass
(`measure_memory`), a tool's JSON over the files it reads (`instrument`), the comparison of two diff sides
(`diff_verdict`) and its gate (`diff_test`), the record of a build output that `bazel run` stores (`results_store`),
and the workspace's stamp alone (`workspace_stamp`), for a program that stores what it measured itself. Every target
that stores keys its samples by its own label, `@<workspace>//<package>:<name>` (`workspace`, the repository's module
name). Docs: README.md#timing, README.md#bundles, README.md#the-diff, README.md#instruments.

    timing_bundle(name = "bundle", data = [...], layout = {...}, binds = ["fsla_cu13._C"], venv = VENV, python = ...)
    timing_entry(name = "entmax_solve_<cell>", bundle = ":bundle", executor = "measure.provider:timed",
                 params = {"arm": "entmax_solve"}, cells = ["@fsla_cells//:<cell>"])
    interleaved_timing(name = "session_entmax", entries = [":entmax_solve_<cell>"], clock = ":clock", workspace = "fsla",
                       venv = VENV)
    interleaved_timing(name = "null_entmax", entries = [...], clock = ":clock", null_gate = True, workspace = "fsla", ...)
    measure_memory(name = "memory", entries = [...], workspace = "fsla", venv = VENV)
    diff_side(name = "side_oracle", bundle = ":side_bundle", executor = "tests.oracle.sides:oracle", cells = [...])
    diff_verdict(name = "diff_oracle", left = "@candidate//:side_oracle", right = "@reference//:side_oracle",
                 strategy = "bit-identical", venv = VENV)
    diff_test(name = "diff_oracle_test", verdict = ":diff_oracle", venv = VENV)
    instrument(name = "oracle_tier", tool = "oracle_tier.py", data = [...], devices = "@host_gpu//:identity.txt", venv = VENV)
    results_store(name = "store_oracle_tier", src = ":oracle_tier", workspace = "fsla", venv = VENV, python = ...)

An instrument is a tool run over the files it reads, laid out as a tree (`fsla_devtools.instrument`): its JSON, or
`{"refused": WHY}` when it cannot run, which stops nothing else. Without a device it is an action, its JSON a build
output; on the device it runs in the run phase of the store target that records it, since a build holds the host budget
and the GPU taken under it deadlocks against a measurement, which takes the GPU first. A diff verdict is an instrument
whose tool produces both sides, each on its own bundle's interpreter, and compares them: the sides' tensors never
leave that run. A store target's record keys each output file by its source, its name and the content digest of every
input it depends on, stamps the workspace's commit into its provenance, and its program (`fsla_devtools.record`) asks
the store whether the record already holds the outcome before it writes anything.

Everything a session needs is a build output: each cell's description (its record and the digest of the code that draws
it, never its tensors), each entry's description (written here, nothing imported) and its BUNDLE -- the runfiles (the
code it imports, its library, the files laid out where that code reads them) and `<bundle>.bundle.json` (the
workspace's stamped commit, the bundle's content digest, its environment) -- and the session's manifest. An entry of
another commit is the same description over a bundle that commit's own workspace built and the `bundle` tag copied into
a repository (`exported_entry`), so two commits are entries of ONE session. The interleaving itself, the locks and the
store are the session program's (`fsla_devtools.timing.session`), which the run target's launcher starts on the
environment's interpreter; nothing is timed by an action and nothing outlives the run.

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

TimingBundleInfo = provider(
    doc = "A bundle: the runfiles a tree's entries run in and its manifest.",
    fields = {
        "root": "the bundle's root, the runfiles directory of the workspace that built it",
        "manifest": "its `bundle.json`",
        "python": "the environment's interpreter its workers run on",
        "binds": "the modules a worker must load from inside it",
        "runfiles": "its files, its manifest among them",
    },
)

TimingEntryInfo = provider(
    doc = "A timing entry, a clock reader or a diff side: its description and the runfiles of its bundle.",
    fields = {
        "owner": "the entry's label, the owner of its members in a session's record",
        "kind": "`timed`; `clock`, the in-kernel clock read that proves a session's lock; `side`, a diff side",
        "device": "a side on the device",
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

#: THE STAMP ALONE: the workspace's stamp as a file, for a program that stores what it measured itself (a lab's run)
_STAMP_FILE = _STAMP + """\
ap = argparse.ArgumentParser()
ap.add_argument("--status", type=pathlib.Path)
ap.add_argument("--out", type=pathlib.Path)
a = ap.parse_args()
a.out.write_text(json.dumps(stamp(a.status), indent=1, sort_keys=True) + "\\n")
"""

def _workspace_stamp_impl(ctx):
    out = ctx.actions.declare_file(ctx.label.name + ".stamp.json")
    args = ctx.actions.args()
    args.add_all(["-I", "-c", _STAMP_FILE])
    args.add("--status", ctx.info_file)
    args.add("--out", out)
    ctx.actions.run(
        executable = ctx.file.python,
        arguments = [args],
        inputs = [ctx.info_file],
        outputs = [out],
        mnemonic = "WorkspaceStamp",
        progress_message = "Stamping %{label}",
    )
    return [DefaultInfo(files = depset([out]), runfiles = ctx.runfiles(files = [out]))]

workspace_stamp = rule(
    implementation = _workspace_stamp_impl,
    attrs = {
        "python": attr.label(mandatory = True, allow_single_file = True, cfg = "exec", doc = "the environment's base " +
                                                                                             "interpreter"),
    },
    doc = "The workspace's stamp as `<name>.stamp.json` (commit, branch, checkout, the digest of its uncommitted diff), " +
          "refusing a workspace that stamps no commit: the provenance of what a program stores itself.",
)

def _label(ctx):
    """The label the store keys what this target produces by: `@<workspace>//<package>:<name>`, the workspace the
    repository's module name (`workspace`, `native.module_name()` where the target is declared)."""
    return "@%s//%s:%s" % (ctx.attr.workspace, ctx.label.package, ctx.label.name)

#: the attribute every target that stores takes: its repository's module name
_WORKSPACE = attr.string(mandatory = True, doc = "the repository's module name (`native.module_name()`): the first part " +
                                                 "of the label the store keys this target's samples by")

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

def _timing_bundle_impl(ctx):
    laid_out = {}
    for target, path in ctx.attr.layout.items():
        found = target[DefaultInfo].files.to_list()
        if len(found) != 1:
            fail("%s: %s names %d files, not one" % (ctx.label, target.label, len(found)))
        laid_out[path] = found[0]
    runfiles = ctx.runfiles(files = ctx.files.data, symlinks = laid_out).merge_all(
        [d[DefaultInfo].default_runfiles for d in ctx.attr.data],
    )
    root = ctx.label.workspace_name or ctx.workspace_name

    #: THE BUNDLE'S MANIFEST: the workspace's stamp (a failed status command fails the build) and the content digest
    manifest = ctx.actions.declare_file(ctx.label.name + ".bundle.json")
    files = ctx.actions.args()
    files.use_param_file("@%s", use_always = True)
    files.set_param_file_format("multiline")
    for f in runfiles.files.to_list():
        files.add_all(["--file", _rlocation(ctx, f).removeprefix(root + "/"), f])
    for path, f in laid_out.items():
        files.add_all(["--file", path, f])
    args = ctx.actions.args()
    args.add_all(["-I", "-c", _BUNDLE])
    args.add("--status", ctx.info_file)
    args.add("--venv", ctx.attr.venv)
    args.add("--out", manifest)
    ctx.actions.run(
        executable = ctx.file.python,
        arguments = [args, files],
        inputs = depset([ctx.info_file] + laid_out.values(), transitive = [runfiles.files]),
        outputs = [manifest],
        mnemonic = "TimingBundle",
        progress_message = "Stamping and digesting %{label}'s bundle",
    )
    return [
        DefaultInfo(files = depset([manifest])),
        TimingBundleInfo(root = root, manifest = manifest, python = ctx.attr.venv + "/bin/python3",
                         binds = ctx.attr.binds, runfiles = runfiles.merge(ctx.runfiles(files = [manifest]))),
    ]

timing_bundle = rule(
    implementation = _timing_bundle_impl,
    attrs = {
        "data": attr.label_list(allow_files = True, doc = "the code the executors import and what they read"),
        "layout": attr.label_keyed_string_dict(allow_files = True, doc = "a file laid out at a path of the bundle, where " +
                                                                         "its code reads it"),
        "binds": attr.string_list(doc = "modules a worker must load from inside the bundle, each checked after set-up"),
        "venv": attr.string(mandatory = True, doc = "the environment's store venv, whose interpreter the workers run on"),
        "python": attr.label(mandatory = True, allow_single_file = True, cfg = "exec", doc = "the environment's base " +
                                                                                             "interpreter, for the manifest"),
    },
    doc = "A BUNDLE: the code a tree's entries and sides run, laid out as runfiles, with its manifest (`bundle.json`: " +
          "the stamped commit, the content digest, the environment), digested once for every entry over it.",
)

def _description(owner, kind, device, executor, params, binds, cells, root, manifest, python, source = None):
    blob = {
        "owner": owner,
        "kind": kind,
        "executor": executor,
        "params": params,
        "binds": binds,
        "cells": cells,
        "bundle": {"root": root, "manifest": manifest, "python": python},
    }
    if kind == "side":
        blob["device"] = device
    if source != None:
        blob["source"] = source
    return json.encode_indent(blob) + "\n"

def _timing_entry_impl(ctx):
    kind = ctx.attr.kind
    if (kind == "clock") == bool(ctx.attr.cells):
        fail("%s: a timed entry or a side takes cells and a clock reader none" % ctx.label)
    if ctx.attr.device and kind != "side":
        fail("%s: only a diff side runs on the device as a side; a timed entry always does" % ctx.label)
    bundle = ctx.attr.bundle[TimingBundleInfo]
    cells = [c[CellInfo].description for c in ctx.attr.cells]
    description = ctx.actions.declare_file(ctx.label.name + ".entry.json")
    ctx.actions.write(description, _description(
        _owner(ctx.label),
        kind,
        ctx.attr.device,
        ctx.attr.executor,
        json.decode(ctx.attr.params),
        bundle.binds,
        [_rlocation(ctx, c) for c in cells],
        bundle.root,
        _rlocation(ctx, bundle.manifest),
        bundle.python,
    ))
    runfiles = bundle.runfiles.merge(ctx.runfiles(files = [description] + cells))
    return [
        DefaultInfo(files = depset([description, bundle.manifest]), runfiles = runfiles),
        TimingEntryInfo(owner = _owner(ctx.label), kind = kind, device = ctx.attr.device, description = description,
                        runfiles = runfiles),
    ]

_timing_entry = rule(
    implementation = _timing_entry_impl,
    attrs = {
        "bundle": attr.label(mandatory = True, providers = [TimingBundleInfo], doc = "the bundle it runs in"),
        "executor": attr.string(mandatory = True, doc = "`module:function`: called with a cell's data and `params` in the " +
                                                        "entry's worker, it returns a `Timed` (a clock reader: the GHz; " +
                                                        "a side: its named tensors)"),
        "params": attr.string(default = "{}", doc = "the executor's parameters, as JSON"),
        "kind": attr.string(default = "timed", values = ["timed", "clock", "side"]),
        "device": attr.bool(doc = "a side that runs on the device, so its verdict runs in the run or test phase"),
        "cells": attr.label_list(providers = [CellInfo], doc = "the cells it runs on, a session member each"),
    },
    doc = "One callable on cells in a bundle: a timed entry, the clock reader, or a diff side; its description.",
)

def timing_entry(name, params = {}, **kwargs):
    """A timing entry (`kind = "clock"`: the clock reader); `params` a dict, written as JSON."""
    _timing_entry(name = name, params = json.encode(params), **kwargs)

def diff_side(name, params = {}, **kwargs):
    """A DIFF SIDE: an executor over cells in a bundle, returning `{name: tensor}` a cell; `device` for a side on the
    device. A `diff_verdict` compares two."""
    _timing_entry(name = name, params = json.encode(params), kind = "side", **kwargs)

def _exported_entry_impl(ctx):
    here = ctx.label.workspace_name + "/"
    description = ctx.actions.declare_file(ctx.label.name + ".entry.json")
    ctx.actions.write(description, _description(
        _owner(ctx.label),
        ctx.attr.kind,
        ctx.attr.device,
        ctx.attr.executor,
        json.decode(ctx.attr.params),
        ctx.attr.binds,
        [here + c for c in ctx.attr.cells],
        here + ctx.attr.root,
        here + ctx.attr.manifest,
        ctx.attr.python,
        source = ctx.attr.source,
    ))
    runfiles = ctx.runfiles(files = [description], transitive_files = depset(ctx.files.tree))
    return [
        DefaultInfo(files = depset([description]), runfiles = runfiles),
        TimingEntryInfo(owner = _owner(ctx.label), kind = ctx.attr.kind, device = ctx.attr.device,
                        description = description, runfiles = runfiles),
    ]

exported_entry = rule(
    implementation = _exported_entry_impl,
    attrs = {
        "source": attr.string(mandatory = True, doc = "the entry's label in the workspace that built it"),
        "kind": attr.string(mandatory = True, values = ["timed", "clock", "side"]),
        "device": attr.bool(),
        "executor": attr.string(mandatory = True),
        "params": attr.string(default = "{}", doc = "the executor's parameters, as JSON"),
        "binds": attr.string_list(),
        "cells": attr.string_list(doc = "its cells' descriptions, by their paths in this repository"),
        "root": attr.string(mandatory = True, doc = "the bundle's root, by its path in this repository"),
        "manifest": attr.string(mandatory = True, doc = "the bundle's manifest, by its path in this repository"),
        "python": attr.string(mandatory = True, doc = "the bundle's environment's interpreter"),
        "tree": attr.label(mandatory = True, allow_files = True, doc = "the bundle's files, as the workspace built them"),
    },
    doc = "A timing entry, clock reader or side BUILT IN ANOTHER WORKSPACE, by its own commit's pins (the environment " +
          "extension's `bundle` tag generates these): its description, its paths in this repository's runfiles.",
)

_SESSION = """\
#!/bin/bash
# a timing session (fsla_devtools' {rule}): the session program on the environment's store venv, which carries the
# store; each entry's worker runs on its bundle's interpreter, its bundle its whole import path
set -euo pipefail
runfiles="${{RUNFILES_DIR:-$(realpath -s "$0").runfiles}}"
exec {python} -P -m fsla_devtools.timing.session --runfiles "$runfiles" --manifest {manifest} "$@"
"""

def _session_target(ctx, manifest_fields, members, rule_name):
    manifest = ctx.actions.declare_file(ctx.label.name + ".session.json")
    ctx.actions.write(manifest, json.encode_indent(manifest_fields) + "\n")
    launcher = ctx.actions.declare_file(ctx.label.name + ".sh")
    ctx.actions.write(launcher, _SESSION.format(rule = rule_name, python = shell.quote(ctx.attr.venv + "/bin/python3"),
                                                manifest = shell.quote(_rlocation(ctx, manifest))), is_executable = True)
    runfiles = ctx.runfiles(files = [manifest]).merge_all([m[TimingEntryInfo].runfiles for m in members])
    return [DefaultInfo(executable = launcher, files = depset([manifest]), runfiles = runfiles)]

def _timed_only(ctx, entries):
    for entry in entries:
        if entry[TimingEntryInfo].kind != "timed":
            fail("%s: %s is a %s, not a timed entry" % (ctx.label, entry.label, entry[TimingEntryInfo].kind))
    seen = {}
    for entry in entries:
        owner = entry[TimingEntryInfo].owner
        if owner in seen:
            fail("%s: %s is named twice" % (ctx.label, owner))
        seen[owner] = True

def _interleaved_timing_impl(ctx):
    _timed_only(ctx, ctx.attr.entries)
    if ctx.attr.clock and ctx.attr.clock[TimingEntryInfo].kind != "clock":
        fail("%s: %s is not a clock reader" % (ctx.label, ctx.attr.clock.label))
    return _session_target(ctx, {
        "session": _owner(ctx.label),
        "mode": "null" if ctx.attr.null_gate else "session",
        "claim": ctx.attr.claim,
        "rounds": ctx.attr.rounds,
        "reps": ctx.attr.reps,
        "warmup": ctx.attr.warmup,
        "label": _label(ctx),
        "entries": [_rlocation(ctx, e[TimingEntryInfo].description) for e in ctx.attr.entries],
        "clock": _rlocation(ctx, ctx.attr.clock[TimingEntryInfo].description) if ctx.attr.clock else None,
    }, ctx.attr.entries + ([ctx.attr.clock] if ctx.attr.clock else []), "interleaved_timing")

interleaved_timing = rule(
    implementation = _interleaved_timing_impl,
    executable = True,
    attrs = {
        "entries": attr.label_list(mandatory = True, providers = [TimingEntryInfo], doc = "the timed entries"),
        "clock": attr.label(providers = [TimingEntryInfo], doc = "the clock reader that proves the host's lock"),
        "rounds": attr.int(default = 8),
        "reps": attr.int(default = 11, doc = "odd, so a round's median is a sample"),
        "warmup": attr.int(default = 10, doc = "calls of each member discarded first; at least the program's floor"),
        "null_gate": attr.bool(doc = "THE NULL GATE: each member set up twice, in two workers of its bundle, and the two " +
                                     "copies timed as one session, so a worker's bias is found where their per-rep " +
                                     "ratios put one outside their interquartile range"),
        "workspace": _WORKSPACE,
        "claim": attr.string(doc = "what the session claims of itself; `unlocked` admits a host with no clock lock"),
        "venv": attr.string(mandatory = True, doc = "the environment's store venv, whose interpreter runs the program"),
    },
    doc = "An interleaved timing session over entries, or their null gate: `bazel run` measures and stores it " +
          "(`-- --no-store`, `--scratch DIR`, `--store-root DIR`).",
)

def _measure_memory_impl(ctx):
    _timed_only(ctx, ctx.attr.entries)
    return _session_target(ctx, {
        "session": _owner(ctx.label),
        "mode": "memory",
        "calls": ctx.attr.calls,
        "label": _label(ctx),
        "entries": [_rlocation(ctx, e[TimingEntryInfo].description) for e in ctx.attr.entries],
    }, ctx.attr.entries, "measure_memory")

measure_memory = rule(
    implementation = _measure_memory_impl,
    executable = True,
    attrs = {
        "entries": attr.label_list(mandatory = True, providers = [TimingEntryInfo], doc = "the timed entries"),
        "calls": attr.int(default = 5, doc = "the calls each member's peak is read over"),
        "workspace": _WORKSPACE,
        "venv": attr.string(mandatory = True, doc = "the environment's store venv, whose interpreter runs the program"),
    },
    doc = "A MEMORY PASS over entries: each member alone, the caching allocator's peak over its calls and what it holds " +
          "outside it; `bazel run` measures and stores it.",
)

InstrumentInfo = provider(
    doc = "An instrument: the tool, its arguments and the files it reads, and its output where a build makes it.",
    fields = {
        "tool": "the tool's path in the instrument's tree",
        "args": "its arguments, before `--json OUT`",
        "files": "every file it reads, laid out in its tree at its own path (`_tree_path`)",
        "placed": "(path in the tree, file) of every file it reads at another path: a verdict's sides' runfiles",
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

def _instrument(ctx, tool, args, files, placed, inputs, devices, venv):
    """An instrument's providers: on the device, what its store target or test runs; else the action that runs it."""
    name = ctx.label.name + ".json"
    if devices:
        inputs = inputs + [(_owner(devices.label), devices.files)]

        #: named as the action's output would be, by its path in the tree
        repo = _repository(ctx.label.workspace_name)
        where = (["external", repo] if repo else []) + [ctx.label.package, name]
        return [DefaultInfo(), InstrumentInfo(tool = _tree_path(tool), args = args, files = files, placed = placed,
                                              inputs = inputs, output = None, name = "/".join([w for w in where if w]))]

    #: an instrument without a device is an action: its tree laid out, the tool run in it, its JSON the output
    listing = ctx.actions.args()
    listing.set_param_file_format("multiline")
    listing.add_all(files, map_each = _tree_entry)
    for path, f in placed:
        listing.add_all([path, f])
    files_list = ctx.actions.declare_file(ctx.label.name + ".files")
    ctx.actions.write(files_list, listing)
    output = ctx.actions.declare_file(name)
    run = ctx.actions.args()
    run.add_all(["-I", "-m", "fsla_devtools.instrument", "--files", files_list, "--tool", _tree_path(tool)])
    run.add("--out", output)
    run.add("--")
    run.add_all(args)
    ctx.actions.run(
        executable = venv + "/bin/python3",
        arguments = [run],
        inputs = depset([files_list] + [f for _path, f in placed], transitive = [files]),
        outputs = [output],
        mnemonic = "Instrument",
        progress_message = "Running %{label}'s tool",
        #: the environment's interpreter reads its packages from the store, which the sandbox does not hold; its store
        #: path on the command line keys the action by the environment
        execution_requirements = {"no-sandbox": "1"},
    )
    return [DefaultInfo(files = depset([output])), InstrumentInfo(tool = _tree_path(tool), args = args, files = files,
                                                                  placed = placed, inputs = inputs, output = output,
                                                                  name = _tree_path(output))]

def _instrument_impl(ctx):
    targets = [ctx.attr.tool] + ctx.attr.data
    args = [ctx.expand_location(a, targets) for a in ctx.attr.arguments]
    return _instrument(ctx, ctx.file.tool, args, depset([ctx.file.tool] + ctx.files.data), [],
                       [(_owner(t.label), t.files) for t in targets], ctx.attr.devices, ctx.attr.venv)

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

#: THE VERDICT'S TOOL, written for each verdict (an instrument runs a tool file in its tree): the comparison program
_VERDICT_TOOL = """\
import sys
from fsla_devtools.diff.verdict import main
sys.exit(main(sys.argv[1:]))
"""

def _runfiles_placed(ctx, runfiles):
    """Every file of `runfiles` at its path in a runfiles tree, its laid-out files included."""
    placed = [(_rlocation(ctx, f), f) for f in runfiles.files.to_list()]
    placed += [(ctx.workspace_name + "/" + s.path, s.target_file) for s in runfiles.symlinks.to_list()]
    return placed

def _diff_verdict_impl(ctx):
    left, right = ctx.attr.left[TimingEntryInfo], ctx.attr.right[TimingEntryInfo]
    for side, info in (("left", left), ("right", right)):
        if info.kind != "side":
            fail("%s: its %s, %s, is a %s, not a diff side" % (ctx.label, side, info.owner, info.kind))
    if ctx.attr.minimum < 1:
        fail("%s: a comparison that may compare nothing is not a comparison; minimum = %d" % (ctx.label, ctx.attr.minimum))
    tool = ctx.actions.declare_file(ctx.label.name + ".verdict.py")
    ctx.actions.write(tool, _VERDICT_TOOL)
    args = [
        "--left",
        _rlocation(ctx, left.description),
        "--right",
        _rlocation(ctx, right.description),
        "--strategy",
        ctx.attr.strategy,
        "--params",
        json.encode(json.decode(ctx.attr.params)),
        "--expect",
        ctx.attr.expect,
        "--minimum",
        str(ctx.attr.minimum),
        "--threads",
        str(ctx.attr.threads),
        #: the comparison's code is the environment's fsla_devtools: its name keys the record, as its path keys the action
        "--environment",
        ctx.attr.venv.rstrip("/").split("/")[-1],
    ]
    placed = {}
    for path, f in _runfiles_placed(ctx, left.runfiles) + _runfiles_placed(ctx, right.runfiles):
        placed[path] = f
    devices = ctx.attr.devices if (left.device or right.device) else None
    if (left.device or right.device) and not devices:
        fail("%s: a side on the device needs the devices' identity (`devices`)" % ctx.label)
    return _instrument(ctx, tool, args, depset([tool]), placed.items(), [
        (left.owner, left.runfiles.files),
        (right.owner, right.runfiles.files),
    ], devices, ctx.attr.venv)

_diff_verdict = rule(
    implementation = _diff_verdict_impl,
    attrs = {
        "left": attr.label(mandatory = True, providers = [TimingEntryInfo], doc = "a diff side"),
        "right": attr.label(mandatory = True, providers = [TimingEntryInfo], doc = "a diff side over the same cells"),
        "strategy": attr.string(mandatory = True, values = ["bit-identical", "per-slot", "support-equal"]),
        "params": attr.string(default = "{}", doc = "the strategy's parameters, as JSON"),
        "expect": attr.string(default = "same", values = ["same", "different"], doc = "the claim: an invariant kept, " +
                                                                                       "or a planted difference seen"),
        "minimum": attr.int(default = 1, doc = "the quantities it must compare for its verdict to mean anything"),
        "threads": attr.int(default = 4, doc = "the torch threads each side runs on: a job's share of the host"),
        "devices": attr.label(allow_single_file = True, doc = "the devices' identity, for a side on the device"),
        "venv": attr.string(mandatory = True, doc = "the environment's store venv, whose interpreter compares"),
    },
    doc = "THE DIFF of two sides under one rule (fsla_devtools.diff.verdict): each side produced on its bundle's " +
          "interpreter, the two compared cell by cell, the verdict JSON whether the claim held. An instrument: an " +
          "action over host sides, the run phase of its store or test target over a side on the device.",
)

def diff_verdict(name, params = {}, **kwargs):
    """A diff verdict; `params` a dict, written as JSON."""
    _diff_verdict(name = name, params = json.encode(params), **kwargs)

_DIFF_TEST = """\
#!/bin/bash
# a diff's gate (fsla_devtools' diff_test): its verdict read, or, over a side on the device, run now
set -euo pipefail
runfiles="${{RUNFILES_DIR:-$(realpath -s "$0").runfiles}}"
exec {python} -I -m fsla_devtools.diff.verdict --check --runfiles "$runfiles" --spec {spec}
"""

def _diff_test_impl(ctx):
    instrument = ctx.attr.verdict[InstrumentInfo]
    files = instrument.files.to_list()
    spec = ctx.actions.declare_file(ctx.label.name + ".check.json")
    ctx.actions.write(spec, json.encode_indent({
        "verdict": _owner(ctx.attr.verdict.label),
        "on_difference": ctx.attr.on_difference,
        "output": _rlocation(ctx, instrument.output) if instrument.output else None,
        "tool": instrument.tool,
        "args": instrument.args,
        "files": [[_tree_path(f), _rlocation(ctx, f)] for f in files] +
                 [[path, _rlocation(ctx, f)] for path, f in instrument.placed],
    }) + "\n")
    launcher = ctx.actions.declare_file(ctx.label.name + ".sh")
    ctx.actions.write(launcher, _DIFF_TEST.format(python = shell.quote(ctx.attr.venv + "/bin/python3"),
                                                  spec = shell.quote(_rlocation(ctx, spec))), is_executable = True)
    carried = [instrument.output] if instrument.output else files + [f for _path, f in instrument.placed]
    return [DefaultInfo(executable = launcher, runfiles = ctx.runfiles(files = [spec] + carried))]

diff_test = rule(
    implementation = _diff_test_impl,
    test = True,
    attrs = {
        "verdict": attr.label(mandatory = True, providers = [InstrumentInfo], doc = "a `diff_verdict`"),
        "on_difference": attr.string(default = "fail", values = ["fail", "record"], doc = "a claim not kept fails the " +
                                                                                         "test, or is printed and passes"),
        "venv": attr.string(mandatory = True, doc = "the environment's store venv"),
    },
    doc = "A diff's gate: passes where the verdict's claim held (`on_difference = \"record\"`: always, the verdict " +
          "printed); over a side on the device it runs the verdict itself, in the test phase.",
)

#: THE RECORD'S ACTION: the skeleton Starlark wrote (the source, the label, each output file, an instrument's run),
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
        carried = depset([f for _path, f in instrument.placed], transitive = [instrument.files])
    else:
        found = src[DefaultInfo].files.to_list()
        if not found:
            fail("%s: %s builds no file" % (ctx.label, src.label))
        outputs = [{"name": _tree_path(f), "path": _rlocation(ctx, f)} for f in found]
        carried = depset(found)
    skeleton = ctx.actions.declare_file(ctx.label.name + ".skeleton.json")
    ctx.actions.write(skeleton, json.encode_indent({
        "source": _owner(src.label),
        "label": _label(ctx),
        "outputs": outputs,
        "instrument": None if instrument == None else {
            "tool": instrument.tool,
            "args": instrument.args,
            "device": device,
            "files": [[_tree_path(f), _rlocation(ctx, f)] for f in instrument.files.to_list()] +
                     [[path, _rlocation(ctx, f)] for path, f in instrument.placed] if device else [],
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
        "workspace": _WORKSPACE,
        "inputs": attr.label_list(allow_files = True, doc = "what the output depends on, beyond an instrument's own: " +
                                                            "their files' digests key the record"),
        "venv": attr.string(mandatory = True, doc = "the environment's store venv, whose interpreter runs the store"),
        "python": attr.label(mandatory = True, allow_single_file = True, cfg = "exec", doc = "the environment's base " +
                                                                                             "interpreter, for the record"),
    },
    doc = "The record of a build output: `bazel run` stores each of its files unless the store already holds it " +
          "(`-- --store-root DIR`, `--no-store`, `--scratch DIR`).",
)
