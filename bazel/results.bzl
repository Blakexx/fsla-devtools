"""THE RESULTS STORE'S RULES: what a data repository builds of its records (`fsla_devtools.results`).

    load("@fsla_devtools//bazel:results.bzl", "results_index")
    results_index(name = "index", records = [":tracked"], directory = "records", venv = VENV)

A generated artifact is never written into a source tree: the index of the records is a build output, never a file
beside them.
"""

load(":rules.bzl", "VENV_ATTR", "environment")

def _results_index_impl(ctx):
    db = ctx.actions.declare_file(ctx.label.name + ".sqlite")
    records = [f for f in ctx.files.records if f.short_path.startswith(ctx.attr.directory + "/")]
    if not records:
        fail("%s: no file of `records` is under %s/" % (ctx.label, ctx.attr.directory))
    mark, env = environment(ctx)
    ctx.actions.run_shell(
        #: the environment by its files: the key keys the index, the mark resolves to the store venv where it runs
        inputs = records + env,
        outputs = [db],
        command = 'venv="$(dirname "$(readlink -f "$1")")" && PYTHONDONTWRITEBYTECODE=1 "$venv/bin/python3" ' +
                  '-m fsla_devtools.results --root "$2" index --db "$3" >/dev/null',
        arguments = [mark.path, ctx.attr.directory, db.path],
        mnemonic = "ResultsIndex",
        progress_message = "indexing %d stored files of %s" % (len(records), ctx.attr.directory),
    )
    return [DefaultInfo(files = depset([db]))]

results_index = rule(
    implementation = _results_index_impl,
    attrs = {
        "records": attr.label_list(allow_files = True, mandatory = True, doc = "the files of the records directory"),
        "directory": attr.string(default = "records", doc = "the records directory, from the workspace's root"),
        "venv": VENV_ATTR,
    },
    doc = "The records' SQLite index (`fsla_devtools.results index`): the tables and the views, a build output.",
)
