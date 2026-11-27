"""THE GRAPH'S ENVIRONMENT, every FSLA repository's: a module extension whose tags make the repositories a workspace's
graph runs on -- a Python environment built from a lock into the machine's shared store (`python`), a release pinned
by digest (`pinned`), the machine's devices' identity (`host_gpu`), a pinned
CUDA toolkit (`cuda_toolkit`), the checkout's tracked files (`tree`), the data repository at one commit (`data`),
every central cell's description as a target (`cells`, `bazel/measure.bzl`'s `cell`), the doc references' tool
(`docrefs`, `bazel/rules.bzl`'s `docrefs_test`), and the timing entries and diff sides of another fsla commit or a local
checkout, built in its own workspace by its own pins (`bundle`, `bazel/measure.bzl`'s `exported_entry`). The root module names each
repository and hands it the workspace's own pins and files; nothing here names a workspace. Docs: README.md#the-build-graph.

    environment = use_extension("@fsla_devtools//bazel:environment.bzl", "environment")
    environment.pinned(name = "uv", pin = "@fsla_devtools//pins:uv_pin.json", build = UV_BUILD)
    environment.python(name = "env", lock = "//:requirements.lock", uv = "@uv//:uv",
                       uv_pin = "@fsla_devtools//pins:uv_pin.json")
    environment.tree(name = "tree")
    use_repo(environment, "env", "tree", "uv")
"""

#: REPRODUCIBLE REPOSITORIES (`repo_metadata(reproducible = True)`): a repository whose content is fixed by its attributes
#: and the files it reads -- a release by its digest (`pinned`, `cuda_toolkit`), a commit's archive by its digest
#: (`data`), an environment by its lock's key
#: (`python`: the store's venv is the lock's and pins', the repository only links it) -- is unpacked once into Bazel's
#: repo contents cache, which every workspace on the machine shares, and each output base links it. `host_gpu`
#: and `tree` are not: they read the machine's devices and the workspace's git index, so each workspace makes its own;
#: nor is `cells`, a few kilobytes generated from the registry, nor `docrefs`, one file copied, nor `bundle`, another
#: workspace's build copied.

def _store(rctx):
    store = rctx.getenv("FSLA_GRAPH_STORE")
    if not store:
        fail("FSLA_GRAPH_STORE is unset: run Bazel through its front door, `bazel` (fsla_devtools.graph.front_door)")
    return store

#: THE PINNED INTERPRETER A REPOSITORY RULE RUNS ITS SCRIPT ON (the environment's builder, the cell registry, a bundle's
#: export): uv's pin's CPython, which uv installs into the store, never the machine's `python3`
_UV_ATTRS = {
    "uv": attr.label(mandatory = True, allow_single_file = True, doc = "the pinned uv, which installs the interpreter"),
    "uv_pin": attr.label(mandatory = True, doc = "uv's pin, whose `python` names the interpreter"),
}

def _interpreter(rctx):
    """The pin's CPython in the store (`uv python install`, then `uv python find`), as the environment's builder installs
    it: uv's interpreters and cache are the store's, never the user's."""
    store = _store(rctx)
    version = json.decode(rctx.read(rctx.attr.uv_pin))["python"]
    uv = str(rctx.path(rctx.attr.uv))
    env = {"UV_PYTHON_INSTALL_DIR": store + "/python", "UV_CACHE_DIR": store + "/uv-cache",
           "UV_PYTHON_PREFERENCE": "only-managed", "UV_NO_CONFIG": "1"}
    installed = rctx.execute([uv, "python", "install", "--no-bin", version], environment = env, timeout = 1800)
    found = rctx.execute([uv, "python", "find", version], environment = env)
    if installed.return_code or found.return_code:
        fail("the pinned CPython %s: %s" % (version, installed.stderr + found.stderr))
    return found.stdout.strip()

_PYTHON_BUILD = """\
package(default_visibility = ["//visibility:public"])

#: the interpreter the graph's actions run: the environment's base Python (a link it resolves to find its standard
#: library), standard library only
filegroup(name = "python", srcs = ["bin/python3"])
"""

#: THE ENVIRONMENT BY LABEL, every environment repository's whatever its BUILD file (`VENV` in its `flags.bzl` names it):
#: the store venv's completion mark through the repository's link to the venv -- an action, a test or a run resolves the
#: venv from it where it runs, so no command or script names the store's machine path -- and the environment's key, the
#: file that keys what runs on it
_VENV_BUILD = """
filegroup(name = "venv", srcs = ["venv/.complete", "environment.key"], visibility = ["//visibility:public"])
"""

def _build(rctx, default):
    """The repository's BUILD file: `build_file`'s text, else `build`'s, else `default`."""
    if rctx.attr.build_file:
        return rctx.read(rctx.attr.build_file)
    return rctx.attr.build or default

def _python_impl(rctx):
    pin = json.decode(rctx.read(rctx.attr.uv_pin))
    index = rctx.attr.index
    if rctx.attr.index_from:
        index = json.decode(rctx.read(rctx.attr.index_from))[rctx.attr.index_field]
    #: the environment's inputs: a change to any of them is a new environment
    facts_inputs = ([rctx.attr.post] if rctx.attr.post else []) + rctx.attr.post_args
    for source in [rctx.attr.lock, rctx.attr.make_env] + rctx.attr.watch + facts_inputs + ([rctx.attr.index_from] if rctx.attr.index_from else []):
        rctx.watch(rctx.path(source))
    for source in rctx.attr.watch_trees:
        rctx.watch_tree(rctx.path(source).dirname)
    packages = []
    if rctx.attr.devtools:
        #: fsla_devtools from this module's own source -- the pinned commit's, or the machine's checkout overriding it --
        #: staged as what its install reads, so the environment's key is the package's and not the repository's tests
        devtools = rctx.path(rctx.attr._devtools).dirname
        rctx.watch_tree(devtools.get_child("fsla_devtools"))
        staged = rctx.path("devtools_package")
        rctx.delete(staged)
        rctx.execute(["mkdir", "-p", str(staged)])
        for name in _DEVTOOLS_INSTALLS:
            source = devtools.get_child(name)
            if source.exists:
                rctx.watch_tree(source) if source.is_dir else rctx.watch(source)
                copied = rctx.execute(["cp", "-rL", str(source), str(staged.get_child(name))])
                if copied.return_code != 0:
                    fail("staging %s: %s" % (source, copied.stderr))
        packages.append(str(staged))
    packages += rctx.attr.packages
    args = [
        _interpreter(rctx),
        rctx.path(rctx.attr.make_env),
        "--store",
        _store(rctx),
        "--uv",
        str(rctx.path(rctx.attr.uv)),
        "--python",
        pin["python"],
        "--lock",
        str(rctx.path(rctx.attr.lock)),
        "--index",
        index,
    ]
    for package in packages:
        args += ["--package", package]
    if not rctx.attr.isolated_builds:
        args.append("--no-build-isolation")
    #: the store's bound, from the repository environment without recording it: a changed bound is not a new environment
    for flag, name in (("--max-bytes", "FSLA_GRAPH_STORE_MAX_BYTES"), ("--max-age-days", "FSLA_GRAPH_STORE_MAX_AGE_DAYS")):
        if rctx.os.environ.get(name):
            args += [flag, rctx.os.environ[name]]
    built = rctx.execute(args, timeout = 7200, quiet = False)
    if built.return_code != 0:
        fail("the environment's store build failed (fsla_devtools.graph.make_env):\n" + built.stderr)
    paths = json.decode(built.stdout.strip().splitlines()[-1])
    rctx.symlink(paths["site"], "site-packages")
    rctx.symlink(paths["include"], "python_include")
    rctx.symlink(paths["base"] + "/bin/python3", "bin/python3")
    #: the repository is links into the store: a venv swept from the store (fsla_devtools.graph.make_env) invalidates it
    rctx.watch(paths["venv"] + "/.complete")
    rctx.symlink(paths["venv"], "venv")
    rctx.file("environment.key", paths["venv"].rstrip("/").rsplit("/", 1)[1] + "\n")
    facts = ""
    if rctx.attr.post:
        environment = {}
        for name in rctx.attr.post_env:
            value = rctx.getenv(name)
            if not value:
                fail("%s is unset: run Bazel through its front door, `bazel` (fsla_devtools.graph.front_door)" % name)
            environment[name] = value
        post = rctx.execute([paths["python"], rctx.path(rctx.attr.post)] + [str(rctx.path(f)) for f in rctx.attr.post_args],
                            timeout = 600, environment = environment)
        if post.return_code != 0:
            fail("the environment's facts (%s) failed:\n%s" % (rctx.attr.post, post.stderr))
        facts = post.stdout
    #: the environment by label (`_VENV_BUILD`): a rule's `venv` attribute takes it
    rctx.file("flags.bzl", facts + "VENV = %s\n" % json.encode("@@%s//:venv" % rctx.name))
    rctx.file("BUILD.bazel", _build(rctx, _PYTHON_BUILD) + _VENV_BUILD)
    return rctx.repo_metadata(reproducible = True)

#: what fsla_devtools' install reads of its repository: the project file, the files it names, the package
_DEVTOOLS_INSTALLS = ("pyproject.toml", "LICENSE", "README.md", "fsla_devtools")

_python_attrs = {
    "lock": attr.label(mandatory = True, doc = "the hash-pinned lock (`uv pip compile --generate-hashes`)"),
    "uv": attr.label(mandatory = True, allow_single_file = True, doc = "the pinned uv the store is built with"),
    "uv_pin": attr.label(mandatory = True, doc = "uv's pin, whose `python` names the interpreter uv installs"),
    "index": attr.string(default = "", doc = "an extra index the lock's packages come from (a torch index)"),
    "index_from": attr.label(allow_single_file = True, doc = "or a JSON file whose `index_field` names that index"),
    "index_field": attr.string(default = "torch_index", doc = "the field of `index_from` that names the index"),
    "devtools": attr.bool(default = True, doc = "fsla_devtools from this module's source, after the lock"),
    "packages": attr.string_list(doc = "requirements pinned by commit (`name @ git+URL@COMMIT`), after the lock"),
    "isolated_builds": attr.bool(default = True, doc = "build each package apart from the environment; False builds " +
                                                         "against it, for a package whose build imports a locked one " +
                                                         "(the lock then carries the build backend)"),
    "post": attr.label(doc = "a script the environment's interpreter runs, its output the facts flags.bzl opens with"),
    "post_args": attr.label_list(allow_files = True, doc = "the script's arguments, each a file's path"),
    "post_env": attr.string_list(doc = "the repository environment variables the script reads"),
    "watch": attr.label_list(allow_files = True, doc = "the further files whose change is a new environment"),
    "watch_trees": attr.label_list(allow_files = True, doc = "files whose whole directory's change is a new environment"),
    "build": attr.string(doc = "the repository's BUILD file (the environment's interpreter alone by default)"),
    "build_file": attr.label(allow_single_file = True, doc = "or a file holding it"),
}

_python_repo = repository_rule(
    implementation = _python_impl,
    attrs = _python_attrs | {
        "make_env": attr.label(default = "//fsla_devtools/graph:make_env.py"),
        "_devtools": attr.label(default = "//:pyproject.toml"),
    },
    doc = "A Python environment: a lock built once a key into the shared store, and `flags.bzl` with its venv by label.",
)

def _pinned_impl(rctx):
    pin = json.decode(rctx.read(rctx.attr.pin))
    asset = pin["release_asset"]
    #: an archive (a .tar.gz, a .tar.xz or a wheel, a zip) unpacks from its pin's folder (`archive_root`); a bare binary lands
    #: at its `binary_path`
    wheel = asset.endswith(".whl")
    archive = "zip" if wheel else "tar.xz" if asset.endswith(".tar.xz") else "tar.gz"
    #: the release URL, then its mirrors: the same asset through the host's API, which serves it as octet-stream
    urls = [pin["release_url"]] + pin.get("mirror_urls", [])
    headers = {"Accept": ["application/octet-stream"]}
    if pin.get("asset_kind") == "binary":
        rctx.download(url = urls, sha256 = pin["asset_sha256"], output = pin["binary_path"], executable = True, headers = headers)
    else:
        rctx.download_and_extract(
            url = urls,
            sha256 = pin["asset_sha256"],
            type = archive,
            stripPrefix = pin["archive_root"],
            headers = headers,
        )
    if "binary_sha256" in pin:
        binary = str(rctx.path(pin["binary_path"]))
        digest = rctx.execute(["sha256sum", binary]).stdout.split(" ")[0]
        if digest != pin["binary_sha256"]:
            fail("%s: %s's sha256 is %s, not the pin's %s" % (pin["name"], pin["binary_path"], digest, pin["binary_sha256"]))
        rctx.execute(["chmod", "0755", binary])
    #: the repository's BUILD file names the binary by its pin
    rctx.file("BUILD.bazel", _build(rctx, "").replace("{binary}", pin["binary_path"]))
    return rctx.repo_metadata(reproducible = True)

_pinned_attrs = {
    "pin": attr.label(mandatory = True, doc = "the pin: its release, digest and folder"),
    "build": attr.string(doc = "the repository's BUILD file, `{binary}` the pin's binary path"),
    "build_file": attr.label(allow_single_file = True, doc = "or a file holding it"),
}

_pinned_repo = repository_rule(
    implementation = _pinned_impl,
    attrs = _pinned_attrs,
    doc = "A pinned release, downloaded by its pin's digest.",
)

def _host_gpu_impl(rctx):
    #: only a device tier reads this repository, and a device tier on a machine that cannot name its devices refuses
    smi = rctx.which("nvidia-smi")
    if not smi:
        fail("no nvidia-smi on PATH: a device test tier is keyed on the machine's devices -- load the NVIDIA driver, " +
             "whose nvidia-smi names them, or run a tier that needs none")
    #: read again when the driver moves: its tools move with it
    rctx.watch(smi)
    found = rctx.execute([smi, "--query-gpu=index,name,uuid,driver_version,compute_cap,memory.total", "--format=csv,noheader"])
    if found.return_code != 0 or not found.stdout.strip():
        fail("nvidia-smi names no device (exit %d): %s -- a device test tier needs one" % (found.return_code, found.stderr))
    rctx.file("identity.txt", "# THE MACHINE'S DEVICES, an input of every device test tier\n" + found.stdout)
    rctx.file("BUILD.bazel", 'exports_files(["identity.txt"], visibility = ["//visibility:public"])\n')

_host_gpu_repo = repository_rule(
    implementation = _host_gpu_impl,
    #: read at every fetch, so a device swapped under one driver is the key's too
    local = True,
    doc = "The machine's devices (each one's name, UUID, driver and compute capability), an input of every device " +
          "test tier: a pass is the device's, never replayed for another.",
)

def _component(rctx, root, entry, output, license):
    """One redistributable component by the manifest's digest, its archive's own folder stripped, its LICENSE kept at
    `license` (every component carries one at the same path)."""
    folder = entry["relative_path"].rsplit("/", 1)[1].removesuffix(".tar.xz")
    rctx.download_and_extract(
        url = root + "/" + entry["relative_path"],
        sha256 = entry["sha256"],
        stripPrefix = folder,
        #: a rename comes before the folder is stripped, so the new name is under the folder too
        rename_files = {folder + "/LICENSE": folder + "/" + license},
        output = output,
    )

def _cuda_toolkit_impl(rctx):
    pin = json.decode(rctx.read(rctx.attr.pin))
    rctx.download(url = pin["manifest_url"], sha256 = pin["manifest_sha256"], output = "redistrib.json")
    redist = json.decode(rctx.read("redistrib.json"))
    root = pin["manifest_url"].rsplit("/", 1)[0]
    #: every component in one tree, as the machine's own installer lays it out, then the pin's links
    for component in pin["components"]:
        _component(rctx, root, redist[component]["linux-x86_64"], ".", "licenses/%s/LICENSE" % component)
    for link, target in pin["links"].items():
        rctx.symlink(target, link)
    #: further components each in a folder of its own, the BUILD file's to expose (their headers, their tools)
    for component, folder in rctx.attr.beside.items():
        _component(rctx, root, redist[component]["linux-x86_64"], folder, "LICENSE")
    for path in rctx.attr.delete:
        rctx.delete(path)
    rctx.file("BUILD.bazel", _build(rctx, ""))
    return rctx.repo_metadata(reproducible = True)

_cuda_toolkit_attrs = {
    "pin": attr.label(mandatory = True, doc = "the redistributable manifest by its digest, the components and links"),
    "beside": attr.string_dict(doc = "further components, each into the folder named"),
    "delete": attr.string_list(doc = "paths removed after the unpack"),
    "build": attr.string(doc = "the repository's BUILD file"),
    "build_file": attr.label(allow_single_file = True, doc = "or a file holding it"),
}

_cuda_toolkit_repo = repository_rule(
    implementation = _cuda_toolkit_impl,
    attrs = _cuda_toolkit_attrs,
    doc = "A pinned CUDA toolkit: NVIDIA's redistributable manifest by its digest, each component by the manifest's.",
)

def _tree_impl(rctx):
    root = str(rctx.workspace_root)
    git = rctx.which("git")
    #: the commit gate's tree is the commit's index (a partial commit's is not the checkout's): the hook hands it in
    index_file = rctx.getenv("GIT_INDEX_FILE")
    env = {"GIT_INDEX_FILE": index_file} if index_file else {}
    index = rctx.execute([git, "-C", root, "rev-parse", "--path-format=absolute", "--git-path", "index"], environment = env).stdout.strip()
    #: re-read when the index moves: a file added, removed or staged
    rctx.watch(index)
    listed = rctx.execute([git, "-C", root, "ls-files", "-z"], environment = env)
    if listed.return_code != 0:
        fail("git ls-files failed in %s: %s" % (root, listed.stderr))
    files = [f for f in listed.stdout.split("\0") if f]
    packages = sorted([f.rsplit("/", 1)[0] if "/" in f else "" for f in files if f.rsplit("/", 1)[-1] == "BUILD.bazel"])
    tracked = {pkg: [] for pkg in packages}
    for f in files:
        owner = ""
        for pkg in packages:
            if pkg and f.startswith(pkg + "/") and len(pkg) > len(owner):
                owner = pkg
        tracked[owner].append(f[len(owner) + 1:] if owner else f)
    rctx.file("tree.bzl", "\"\"\"GENERATED from `git ls-files`: the checkout's tracked files, by the package that holds them.\"\"\"\n\nTRACKED = %s\n" % json.encode_indent(tracked, indent = "    "))
    rctx.file("BUILD.bazel", 'exports_files(["tree.bzl"], visibility = ["//visibility:public"])\n')

_tree_repo = repository_rule(
    implementation = _tree_impl,
    doc = "The checkout's tracked files (`git ls-files`), by package: the tree the lints read, re-read when the index moves.",
)

#: what of a data repository's commit is data: everything else is its own graph (its gate), not this one's, and is
#: dropped; the repository's BUILD file is `data.BUILD`
_DATA = ("records",)

def _data_impl(rctx):
    name = rctx.attr.remote.rstrip("/").split("/")[-1]
    rctx.download_and_extract(
        url = "%s/archive/%s.tar.gz" % (rctx.attr.remote.rstrip("/"), rctx.attr.commit),
        sha256 = rctx.attr.sha256,
        stripPrefix = "%s-%s" % (name, rctx.attr.commit),
    )
    for entry in rctx.path(".").readdir():
        if entry.basename not in _DATA:
            rctx.delete(entry)
    rctx.file("BUILD.bazel", rctx.read(rctx.attr._build))
    return rctx.repo_metadata(reproducible = True)

_data_attrs = {
    "remote": attr.string(mandatory = True, doc = "the data repository's public URL (a GitHub repository's)"),
    "commit": attr.string(mandatory = True, doc = "the commit every citation and reader takes the data at"),
    "sha256": attr.string(mandatory = True, doc = "the digest of that commit's archive"),
}

_data_repo = repository_rule(
    implementation = _data_impl,
    attrs = _data_attrs | {"_build": attr.label(default = "//bazel:data.BUILD")},
    doc = "The data repository at one commit, by its archive's digest: its `records/`, nothing of its graph.",
)

_CELLS_BUILD = """\
\"\"\"GENERATED from fsla_devtools' central cell registry (`fsla_devtools.cells.descriptions`): one target a cell, its
description.\"\"\"

load("{rules}", "cell")

package(default_visibility = ["//visibility:public"])
"""

_CELLS_BZL = """\
\"\"\"GENERATED from fsla-devtools' central cell registry (`fsla_devtools.cells.facts`): every cell's fields in the one form,
by name, so a workspace's macros select cells by their facts and never by a name's spelling.\"\"\"

"""

def _cells_impl(rctx):
    root = rctx.path(rctx.attr._devtools).dirname
    #: re-read when the registry or its drawing code moves: the draw digest is over the whole package
    rctx.watch_tree(root.get_child("fsla_devtools").get_child("cells"))
    found = rctx.execute([_interpreter(rctx), "-B", "-S", "-c", "import json, sys\nsys.path.insert(0, sys.argv[1])\n" +
                          "from fsla_devtools.cells import descriptions, facts\n" +
                          "print(json.dumps({'descriptions': descriptions(), 'facts': repr(facts())}))", str(root)])
    if found.return_code != 0:
        fail("the central cell registry did not load:\n" + found.stderr)
    registry = json.decode(found.stdout)
    build = [_CELLS_BUILD.format(rules = str(Label("//bazel:measure.bzl")))]
    for name, description in registry["descriptions"].items():
        rctx.file(name + ".json", json.encode_indent(description) + "\n")
        build.append('cell(name = "%s", src = "%s.json")\n' % (name, name))
    rctx.file("BUILD.bazel", "\n".join(build))
    #: the fields as Python writes them, which Starlark reads: dicts, tuples, strings, numbers, None and booleans
    rctx.file("cells.bzl", _CELLS_BZL + "CELLS = " + registry["facts"] + "\n")

_cells_repo = repository_rule(
    implementation = _cells_impl,
    attrs = _UV_ATTRS | {"_devtools": attr.label(default = "//:pyproject.toml")},
    doc = "Every central cell as a target (`@<name>//:<cell>`): its description, never its tensors; and every cell's " +
          "fields as Starlark (`@<name>//:cells.bzl`'s `CELLS`).",
)

def _docrefs_impl(rctx):
    #: a copy, re-made when the tool changes (a label's read is watched): a workspace's build cannot name a file of this
    #: module's own packages, whose BUILD files load the graph this module has only as the root
    rctx.file("docrefs.py", rctx.read(rctx.attr._tool))
    rctx.file("BUILD.bazel", 'exports_files(["docrefs.py"], visibility = ["//visibility:public"])\n')

_docrefs_repo = repository_rule(
    implementation = _docrefs_impl,
    attrs = {"_tool": attr.label(default = "//:fsla_devtools/docrefs.py")},
    doc = "The doc references' tool (`@<name>//:docrefs.py`, `fsla_devtools.docrefs`), which a `docrefs_test` runs.",
)

_BUNDLE_BUILD = """\
\"\"\"GENERATED by fsla-devtools' `bundle` tag (bazel/environment.bzl): the timing entries, clock readers and diff
sides of {what}, built in its own workspace by its own pins, its bundles' files copied into `tree/`.\"\"\"

load("{rules}", "exported_entry")

package(default_visibility = ["//visibility:public"])

#: the bundles' files, and each environment's completion mark through the link to its venv (fsla_devtools.graph.export)
filegroup(
    name = "tree",
    srcs = glob(["tree/**", "venvs/*/.complete"]),
)
"""

def _bundle_impl(rctx):
    store = _store(rctx)
    devtools = rctx.path(rctx.attr._devtools).dirname
    rctx.watch_tree(devtools.get_child("fsla_devtools").get_child("graph"))
    args = [_interpreter(rctx), "-B", "-S", str(rctx.path(rctx.attr._export)), "--out", str(rctx.path(".")),
            "--cquery", str(rctx.path(rctx.attr._cquery))]
    environment = {}
    if rctx.attr.commit:
        if not rctx.attr.remote:
            fail("bundle %s: a commit is fetched from its `remote`" % rctx.attr.name)
        args += ["--commit", rctx.attr.commit, "--remote", rctx.attr.remote, "--store", store]
        what = "commit %s of %s" % (rctx.attr.commit, rctx.attr.remote)
        #: a commit builds by ITS OWN PINS: the front door overrides no module with the machine's checkout, or an old
        #: commit would build against today's rules
        environment["FSLA_GRAPH_OWN_PINS"] = "1"
    else:
        checkout = rctx.getenv(rctx.attr.worktree_env)
        if not checkout:
            fail("bundle %s: %s is unset; name the checkout this run measures: --repo_env=%s=PATH" %
                 (rctx.attr.name, rctx.attr.worktree_env, rctx.attr.worktree_env))
        #: a working tree changes under the repository: a change to a tracked file, or to the index (a file added or
        #: removed), is built again (incrementally) and copied. Each file is watched, never the tree, whose output links
        #: lead into the build's own output base
        git = ["git", "-C", checkout]
        listed = rctx.execute(git + ["ls-files", "-z"])
        index = rctx.execute(git + ["rev-parse", "--path-format=absolute", "--git-path", "index"])
        if listed.return_code or index.return_code:
            fail("bundle %s: %s is no git checkout: %s" % (rctx.attr.name, checkout, listed.stderr + index.stderr))
        rctx.watch(index.stdout.strip())
        for f in listed.stdout.split("\0"):
            if f and rctx.path(checkout + "/" + f).exists:
                rctx.watch(checkout + "/" + f)
        args += ["--checkout", checkout]
        what = "the checkout %s names" % rctx.attr.worktree_env
    for package in rctx.attr.packages:
        args += ["--package", package]
    rctx.report_progress("building the bundles of %s" % what)
    built = rctx.execute(args, environment = environment, timeout = 6 * 3600, quiet = False)
    if built.return_code != 0:
        fail("bundle %s: the build of %s failed (fsla_devtools.graph.export):\n%s" % (rctx.attr.name, what, built.stderr))
    exported = json.decode(built.stdout.strip().splitlines()[-1])
    #: a bundle's interpreter is an environment of the store: one swept from it fetches the bundle again
    for venv in exported["venvs"]:
        rctx.watch(venv + "/.complete")
    rctx.file("BUILD.bazel", _BUNDLE_BUILD.format(what = what, rules = str(Label("//bazel:measure.bzl"))) + "\n" +
              rctx.read("exports.BUILD"))
    rctx.delete("exports.BUILD")

_bundle_attrs = _UV_ATTRS | {
    "commit": attr.string(doc = "the commit built, fetched from `remote` into the store's checkouts"),
    "remote": attr.string(doc = "the repository the commit is fetched from"),
    "worktree_env": attr.string(doc = "or a local checkout, built in place: the repository environment variable the " +
                                      "run names it by (`--repo_env=NAME=PATH`), since a module names no machine path"),
    "packages": attr.string_list(default = ["//measure:all"], doc = "the target patterns whose timing entries, clock " +
                                                                    "readers and diff sides are exported"),
}

_bundle_repo_attrs = _bundle_attrs | {
    "_devtools": attr.label(default = "//:pyproject.toml"),
    "_export": attr.label(default = "//fsla_devtools/graph:export.py"),
    "_cquery": attr.label(default = "//fsla_devtools/graph:export.cquery"),
}

_bundle_commit_repo = repository_rule(
    implementation = _bundle_impl,
    attrs = _bundle_repo_attrs,
    doc = "A commit's timing entries and diff sides, built in a checkout of the commit by its own pins.",
)

_bundle_worktree_repo = repository_rule(
    implementation = _bundle_impl,
    attrs = _bundle_repo_attrs,
    local = True,
    doc = "A local checkout's timing entries and diff sides, built in place, again whenever the checkout changes.",
)

def _bundle(tag):
    if bool(tag.commit) == bool(tag.worktree_env):
        fail("bundle %s: exactly one of `commit` (with `remote`) and `worktree_env`" % tag.name)
    repo = _bundle_commit_repo if tag.commit else _bundle_worktree_repo
    repo(name = tag.name, **{name: getattr(tag, name) for name in _bundle_attrs})

def _named(attrs):
    return attrs | {"name": attr.string(mandatory = True, doc = "the repository's name")}

_TAGS = {
    "python": (_python_repo, _named(_python_attrs)),
    "pinned": (_pinned_repo, _named(_pinned_attrs)),
    "host_gpu": (_host_gpu_repo, _named({})),
    "cuda_toolkit": (_cuda_toolkit_repo, _named(_cuda_toolkit_attrs)),
    "tree": (_tree_repo, _named({})),
    "data": (_data_repo, _named(_data_attrs)),
    "cells": (_cells_repo, _named(_UV_ATTRS)),
    "docrefs": (_docrefs_repo, _named({})),
    "bundle": (None, _named(_bundle_attrs)),
}

def _environment_impl(mctx):
    for mod in mctx.modules:
        if not mod.is_root:
            continue
        for kind, (repo, attrs) in _TAGS.items():
            for tag in getattr(mod.tags, kind):
                if kind == "bundle":
                    _bundle(tag)
                else:
                    repo(**{name: getattr(tag, name) for name in attrs})

environment = module_extension(
    implementation = _environment_impl,
    tag_classes = {kind: tag_class(attrs = attrs) for kind, (_repo, attrs) in _TAGS.items()},
    doc = "The graph's environment, made from the root module's tags: its Python environments, pinned releases, the " +
          "devices' identity, a pinned CUDA toolkit, the checkout's tracked files, the data " +
          "repository at one commit, the central cells' descriptions, the doc references' tool and other commits' " +
          "bundles.",
)
