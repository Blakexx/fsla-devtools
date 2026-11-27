"""THE MEASUREMENT RULES, every FSLA repository's: a cell's description (`cell`, which the environment extension's `cells`
tag declares one of a central cell), a timed callable on cells with the bundle of code it runs in (`timing_entry`), and an
interleaved session over entries that `bazel run` starts and stores (`interleaved_timing`). Docs: README.md#timing.

    timing_entry(name = "entmax_solve_<cell>", executor = "measure.executors:timed", params = {"arm": ...},
                 cells = ["@fsla_cells//:<cell>"], data = [...], binds = ["fsla_cu13._C"], venv = VENV, python = ...)
    interleaved_timing(name = "session_entmax", entries = [":entmax_solve_<cell>"], clock = ":clock")

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

#: THE BUNDLE MANIFEST'S ACTION, run on the environment's base interpreter (standard library only): the workspace's STAMP
#: -- the commit, branch, checkout name and the digest of its uncommitted tracked diff, which its status command writes as
#: the `STABLE_` keys below (`--workspace_status_command`; Bazel fails the build when it fails) -- the bundle's CONTENT
#: DIGEST (sha256 over every file, by its path in the bundle and its bytes' digest) and its ENVIRONMENT (the store venv's
#: key, its path's last part). A workspace that stamps no commit is refused: a number names the code it measured.
_BUNDLE = """\
import argparse, hashlib, json, pathlib
KEYS = {"STABLE_GIT_COMMIT": "git_sha", "STABLE_GIT_BRANCH": "branch", "STABLE_GIT_DIFF_SHA256": "diff_sha256",
        "STABLE_CHECKOUT": "checkout"}
ap = argparse.ArgumentParser(fromfile_prefix_chars="@")
ap.add_argument("--status", type=pathlib.Path)
ap.add_argument("--venv")
ap.add_argument("--out", type=pathlib.Path)
ap.add_argument("--file", nargs=2, action="append", default=[])
a = ap.parse_args()
keys = dict((line + " ").split(" ", 1) for line in a.status.read_text().splitlines() if line)
keys = {k: v.strip() for k, v in keys.items()}
missing = sorted(set(KEYS) - set(keys))
if missing or not keys["STABLE_GIT_COMMIT"]:
    raise SystemExit("the workspace stamps no commit (%s): its .bazelrc names no --workspace_status_command writing %s"
                     % (missing or "STABLE_GIT_COMMIT is empty", sorted(KEYS)))
h = hashlib.sha256()
for name, path in sorted(a.file):
    h.update(name.encode() + b"\\0" + hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest().encode() + b"\\0")
a.out.write_text(json.dumps({"stamp": {field: keys[key] or None for key, field in KEYS.items()}, "digest": h.hexdigest(),
                             "files": len(a.file), "environment": pathlib.PurePath(a.venv).name}, indent=1, sort_keys=True) + "\\n")
"""

def _rlocation(ctx, f):
    """A file's path in a run target's runfiles."""
    return f.short_path[3:] if f.short_path.startswith("../") else ctx.workspace_name + "/" + f.short_path

def _owner(label):
    return ("@%s" % label.workspace_name if label.workspace_name else "") + "//%s:%s" % (label.package, label.name)

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
