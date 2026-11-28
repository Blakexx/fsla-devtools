"""THE HERMETIC CHECK, every FSLA repository's one lint of its graph (`fsla_devtools.hermetic`): `hermetic_test` over a
workspace's gate targets. An aspect reads every action under them -- its mnemonic, command line, environment, a written
file's text and the repositories its inputs come from -- and the test reads those, the workspace's `MODULE.bazel.lock`
(what Bazel records of every module extension's inputs), its `.bazelrc` and the Starlark of its tree and of this module
(the tools repository rules run, which no lockfile records), against the allowlists: this module's default
(`//bazel:hermetic.json`, each entry with why the machine gives it) and the workspace's own. A workspace's gate holds it, so a
tool the graph does not pin cannot land unseen.

    hermetic_test(name = "hermetic", targets = [":library", "//tests:lab"], allowlist = "hermetic.json", venv = VENV)

A workspace with no environment of this module's (a public module that takes this one only as a dev dependency) runs
the check on its own standard-library interpreter instead: `python` (rules_python's `@rules_python//python/bin:python`)
and `check`, the environment extension's `hermetic` repository's `:check`.
"""

load("@bazel_skylib//lib:shell.bzl", "shell")
load(":rules.bzl", "environment", "venv_line")

HermeticInfo = provider(
    doc = "The action rows of a target and everything under it, one JSON file a target and configuration.",
    fields = {"rows": "depset of the row files"},
)

#: the actions whose command line Starlark exposes as None: their content is their output, never a tool's run
_NO_ARGV = ("SymlinkTree", "SourceSymlinkManifest", "RepoMappingManifest", "RunfilesTree", "Symlink",
            "ExecutableSymlink", "SolibSymlink", "UnresolvedSymlink", "TranslateBuildInfo")

def _row(target, action):
    row = {"owner": str(target.label), "mnemonic": action.mnemonic, "argv": action.argv or [], "env": action.env or {}}
    if action.argv == None and action.mnemonic not in _NO_ARGV + ("FileWrite", "TemplateExpand"):
        #: an action Starlark cannot read is a finding of its own, never a silent pass
        row["argv"] = ["<no command line exposed to Starlark>"]
        row["unreadable"] = True
    if action.mnemonic == "FileWrite":
        row["content"] = action.content or ""
    elif action.mnemonic == "TemplateExpand":
        #: what the expansion writes into its template (the template is an input file of its own, read where it is made)
        row["content"] = "\n".join(action.substitutions.values())
    row["repositories"] = sorted({f.owner.workspace_name: None for f in action.inputs.to_list() if f.owner and f.owner.workspace_name}.keys())
    return row

def _hermetic_aspect_impl(target, ctx):
    under = []
    for name in dir(ctx.rule.attr):
        value = getattr(ctx.rule.attr, name)
        deps = value if type(value) == "list" else value.keys() if type(value) == "dict" else [value]
        for dep in deps:
            if type(dep) == "Target" and HermeticInfo in dep:
                under.append(dep[HermeticInfo].rows)
    own = []
    if target.actions:
        #: one file a target and configuration: a target built in two (a transition's) has two runfiles paths
        rows = ctx.actions.declare_file("%s.%d.hermetic.json" % (target.label.name, hash(ctx.bin_dir.path) & 0xffffffff))
        ctx.actions.write(rows, json.encode([_row(target, a) for a in target.actions]))
        own.append(rows)
    return [HermeticInfo(rows = depset(own, transitive = under))]

hermetic_actions = aspect(
    implementation = _hermetic_aspect_impl,
    attr_aspects = ["*"],
    doc = "Every action under a target, as rows the hermetic check reads.",
)

_HERMETIC_TEST = """\
#!/bin/bash
# the hermetic check (fsla_devtools' hermetic_test): the actions under the gate's targets, the lockfile's recorded inputs,
# the settings and the repository rules' Starlark, against the allowlists
set -euo pipefail
{interpreter} --rows {rows} --lock {lock} --bazelrc {bazelrc} --sources {sources} {allowlists}{cc}
"""

#: the check on an environment's venv, which carries this module's package
_ON_VENV = """{venv}exec "$venv/bin/python3" -I -m fsla_devtools.hermetic"""

#: the check on a workspace's own interpreter, the `hermetic` repository's copy of the package first on its path
_ON_PYTHON = ("exec \"$TEST_SRCDIR/_main/{python}\" -B -S -c 'import runpy, sys; sys.path.insert(0, sys.argv.pop(1)); " +
              "runpy.run_module(\"fsla_devtools.hermetic\", run_name=\"__main__\")' \\\n" +
              "  \"$(dirname \"$(dirname \"$(readlink -f \"$TEST_SRCDIR/_main/{check}\")\")\")\"")

def _listing(ctx, name, files):
    listing = ctx.actions.declare_file("%s.%s" % (ctx.label.name, name))
    ctx.actions.write(listing, "".join([f.short_path + "\n" for f in files]))
    return listing

def _hermetic_test_impl(ctx):
    rows = depset(transitive = [t[HermeticInfo].rows for t in ctx.attr.targets]).to_list()
    starlark = [f for f in ctx.files.sources if f.extension == "bzl"]
    rows_list, sources_list = _listing(ctx, "rows", rows), _listing(ctx, "sources", starlark)
    allowlists = [ctx.file._default] + ([ctx.file.allowlist] if ctx.file.allowlist else [])
    if bool(ctx.attr.venv) == bool(ctx.attr.python):
        fail("%s: the check runs on an environment (`venv`) or on an interpreter (`python`, with `check`): exactly one" %
             ctx.label)
    runfiles = ctx.runfiles()
    if ctx.attr.venv:
        mark, env = environment(ctx)
        interpreter = _ON_VENV.format(venv = venv_line(mark))
    else:
        runtime = ctx.attr.python[DefaultInfo]
        python = runtime.files_to_run.executable
        code = [f for f in ctx.files.check if f.short_path.endswith("/fsla_devtools/hermetic.py")]
        if python == None or len(code) != 1:
            fail("%s: `python` is an interpreter and `check` the `hermetic` repository's `:check`" % ctx.label)
        interpreter = _ON_PYTHON.format(python = python.short_path, check = code[0].short_path)
        env = [python] + ctx.files.check
        runfiles = runtime.default_runfiles
    script = ctx.actions.declare_file(ctx.label.name + ".sh")
    ctx.actions.write(script, _HERMETIC_TEST.format(
        interpreter = interpreter,
        rows = shell.quote(rows_list.short_path),
        lock = shell.quote(ctx.file.lock.short_path),
        bazelrc = shell.quote(ctx.file.bazelrc.short_path),
        sources = shell.quote(sources_list.short_path),
        allowlists = " ".join(["--allowlist " + shell.quote(f.short_path) for f in allowlists]),
        cc = " --cc" if ctx.attr.cc else "",
    ), is_executable = True)
    files = rows + starlark + env + allowlists + [rows_list, sources_list, ctx.file.lock, ctx.file.bazelrc]
    return [DefaultInfo(executable = script, runfiles = ctx.runfiles(files = files).merge(runfiles))]

_hermetic_test = rule(
    implementation = _hermetic_test_impl,
    test = True,
    attrs = {
        "targets": attr.label_list(mandatory = True, aspects = [hermetic_actions], doc = "the gate's targets, read whole"),
        "allowlist": attr.label(allow_single_file = [".json"], doc = "the workspace's own allowlist, after the default"),
        "lock": attr.label(mandatory = True, allow_single_file = True, doc = "the workspace's MODULE.bazel.lock"),
        "bazelrc": attr.label(mandatory = True, allow_single_file = True, doc = "the workspace's .bazelrc"),
        "sources": attr.label_list(allow_files = True, doc = "the Starlark the repository rules are written in"),
        "cc": attr.bool(doc = "the workspace compiles C++: its one toolchain is the pinned one"),
        "venv": attr.label(allow_files = True, doc = "the environment, by label (its `flags.bzl`'s `VENV`), the check's interpreter"),
        "python": attr.label(executable = True, cfg = "target", doc = "or a standard-library interpreter the check " +
                                                                      "runs on (with `check`)"),
        "check": attr.label(allow_files = True, doc = "the check's code for `python`: the environment extension's " +
                                                     "`hermetic` repository's `:check`"),
        "_default": attr.label(default = Label("//bazel:hermetic.json"), allow_single_file = True),
    },
)

def hermetic_test(name, targets, venv = None, allowlist = None, cc = False, tree = "//:tree", python = None, check = None,
                  **kwargs):
    """THE HERMETIC CHECK of a workspace: its gate's `targets` read whole, its lockfile, `.bazelrc`, `tree` (its tracked
    files: their `.bzl` are scanned) and this module's own repository rules, against the default allowlist and the
    workspace's `allowlist`; `cc` for a workspace that compiles C++. It runs on `venv` (an environment carrying this
    module's package), or on `python` with `check` (a workspace with no environment of this module's)."""
    _hermetic_test(
        name = name,
        targets = targets,
        allowlist = allowlist,
        lock = "//:MODULE.bazel.lock",
        bazelrc = "//:.bazelrc",
        sources = [tree, Label("//bazel:starlark")],
        cc = cc,
        venv = venv,
        python = python,
        check = check,
        **kwargs
    )
