# THE DATA REPOSITORY'S BUILD FILE in a graph that pins it (fsla_devtools' `environment.data`): its records, every file a
# rule, a reader or a citation takes, at the pinned commit.
package(default_visibility = ["//visibility:public"])

#: the repository's root, named by its one BUILD file: what a tool that resolves a path inside the data is handed
filegroup(
    name = "root",
    srcs = ["BUILD.bazel"],
)

filegroup(
    name = "records",
    srcs = glob(["records/**"]),
)

exports_files(glob(["records/**"]))
