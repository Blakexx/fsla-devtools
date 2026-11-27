"""THE RESULTS STORE'S RULES: what a data repository builds of its records (`fsla_devtools.results`).

    load("@fsla_devtools//bazel:results.bzl", "results_index")
    results_index(name = "index", records = [":tracked"], directory = "records", venv = VENV)

A generated artifact is never written into a source tree: the index of the records is a build output, never a file
beside them.
"""

def _results_index_impl(ctx):
    db = ctx.actions.declare_file(ctx.label.name + ".sqlite")
    records = [f for f in ctx.files.records if f.short_path.startswith(ctx.attr.directory + "/")]
    if not records:
        fail("%s: no file of `records` is under %s/" % (ctx.label, ctx.attr.directory))
    ctx.actions.run_shell(
        inputs = records,
        outputs = [db],
        command = 'PYTHONDONTWRITEBYTECODE=1 "$1/bin/python3" -m fsla_devtools.results --root "$2" index --db "$3" >/dev/null',
        arguments = [ctx.attr.venv, ctx.attr.directory, db.path],
        mnemonic = "ResultsIndex",
        progress_message = "indexing %d stored files of %s" % (len(records), ctx.attr.directory),
    )
    return [DefaultInfo(files = depset([db]))]

results_index = rule(
    implementation = _results_index_impl,
    attrs = {
        "records": attr.label_list(allow_files = True, mandatory = True, doc = "the files of the records directory"),
        "directory": attr.string(default = "records", doc = "the records directory, from the workspace's root"),
        "venv": attr.string(mandatory = True, doc = "the environment's store venv, which carries fsla_devtools"),
    },
    doc = "The records' SQLite index (`fsla_devtools.results index`): the tables and the views, a build output.",
)
