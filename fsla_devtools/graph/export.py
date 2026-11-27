"""THE BUNDLE TAG'S EXPORT: another workspace's timing entries, clock readers and diff sides, built there by its own pins
and copied here, so a workspace measures two commits in ONE session (bazel/environment.bzl's `bundle` tag runs this, on
the machine's python3, standard library only).

    python3 export.py --out REPO --cquery export.cquery (--checkout PATH | --commit SHA --remote URL
                      --store DIR) [--package PATTERN]...

A COMMIT is checked out once under the store (`<store>/checkouts/<sha>`: `git init`, a shallow fetch of the commit, a
detached checkout), and built by its own front door with its own pins -- the caller sets `FSLA_GRAPH_OWN_PINS`, so the
front door overrides no module with the machine's checkout -- and its Bazel server is shut down after; a CHECKOUT is
built in place, incrementally, with the machine's overrides, as the desk's own work. Either way: `bazel cquery` reads
each `_timing_entry` target of the packages (`export.cquery`: its description and its runfiles, every file by its path
there and in the build), `bazel build` builds them, and every file is COPIED into the repository's `tree/` at its
runfiles path -- a copy, since a link into another output base dangles once that base is cleaned. It writes
`exports.BUILD` (one `exported_entry` a target, which the repository rule appends to its BUILD file) and `exports.bzl`
(`ENTRIES`: each target's kind, executor, parameters and cells by name, for a workspace's macros), and prints, last, a
JSON line naming the environments its bundles run on.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

#: files that would make a directory of the repository a package of its own, which its `tree` glob cannot reach
PACKAGE_FILES = ("BUILD", "BUILD.bazel", "WORKSPACE", "WORKSPACE.bazel", "REPO.bazel", "MODULE.bazel")
#: the main repository's directory in a runfiles tree
MAIN = "_main"


def _bazel(checkout: Path, *args: str) -> str:
    bazel = shutil.which("bazel")
    if bazel is None:
        raise SystemExit("no `bazel` on PATH: the bundle is built through the checkout's front door")
    done = subprocess.run([bazel, *args], cwd=checkout, stdout=subprocess.PIPE, text=True, check=False)
    if done.returncode:
        raise SystemExit(f"`bazel {args[0]}` in {checkout} exited {done.returncode}")
    return done.stdout


def check_out(store: Path, commit: str, remote: str) -> Path:
    """The commit's checkout under the store, made once (a lock beside it: two fetches of one commit wait on each
    other)."""
    root = store / "checkouts"
    root.mkdir(parents=True, exist_ok=True)
    checkout, done = root / commit, root / f"{commit}.complete"
    with open(root / f"{commit}.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not done.is_file():
            shutil.rmtree(checkout, ignore_errors=True)
            checkout.mkdir()
            git = ["git", "-C", str(checkout)]
            for step in (["init", "-q"], ["fetch", "-q", "--depth", "1", remote, commit],
                         ["checkout", "-q", "--detach", "FETCH_HEAD"]):
                subprocess.run([*git, *step], check=True)
            head = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
            if head != commit:
                raise SystemExit(f"{remote} served {head} for the commit {commit}: a bundle's commit is named in full")
            done.touch()
    return checkout


def runfiles_path(short_path: str) -> str:
    """A file's path in a runfiles tree."""
    return short_path[3:] if short_path.startswith("../") else f"{MAIN}/{short_path}"


def source(path: str, checkout: Path, execroot: Path, output_base: Path) -> Path:
    """A file of the build, by its path there: an output under the execution root, another repository's file under the
    output base's `external/`, the workspace's own in the checkout."""
    if path.startswith("bazel-out/"):
        return execroot / path
    if path.startswith("external/"):
        return output_base / path
    return checkout / path


def copy(files: dict[str, Path], tree: Path) -> None:
    for path, src in sorted(files.items()):
        if Path(path).name in PACKAGE_FILES:
            raise SystemExit(f"the bundle carries {path}, which would make its directory a package of its own")
        dest = tree / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        shutil.copymode(src, dest)


def starlark(value) -> str:
    """A JSON value as a Starlark literal."""
    if value is None or isinstance(value, bool):
        return {None: "None", True: "True", False: "False"}[value]
    if isinstance(value, dict):
        return "{" + ", ".join(f"{json.dumps(k)}: {starlark(v)}" for k, v in sorted(value.items())) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(starlark(v) for v in value) + "]"
    return json.dumps(value)


def exported(name: str, label: str, d: dict, tree: str, venv: str) -> str:
    """One `exported_entry` of the generated BUILD file, its paths the repository's; `venv` its environment's completion
    mark, by its path here."""
    here = [f"{tree}/{c}" for c in d["cells"]]
    fields = [("name", starlark(name)), ("source", starlark(label)), ("kind", starlark(d["kind"])),
              ("device", starlark(bool(d.get("device")))), ("executor", starlark(d["executor"])),
              ("params", starlark(json.dumps(d["params"], sort_keys=True))), ("binds", starlark(d["binds"])),
              ("cells", starlark(here)), ("root", starlark(f"{tree}/{d['bundle']['root']}")),
              ("manifest", starlark(f"{tree}/{d['bundle']['manifest']}")), ("venv", starlark(venv)),
              ("tree", starlark(":tree"))]
    return "exported_entry(\n" + "".join(f"    {k} = {v},\n" for k, v in fields) + ")\n"


def export(checkout: Path, out: Path, *, cquery: Path, packages: list[str], commit: bool) -> dict:
    pattern = " + ".join(packages)
    lines = _bazel(checkout, "cquery", "--noshow_progress", f'kind("_timing_entry rule", {pattern})',
                   "--output=starlark", f"--starlark:file={cquery}").splitlines()
    targets = [json.loads(line) for line in lines if line.strip()]
    if not targets:
        raise SystemExit(f"{checkout} declares no timing entry, clock reader or diff side in {pattern}: a commit older "
                         "than the timing_entry rule cannot be a bundle")
    labels = [t["label"] for t in targets]
    names = [label.rsplit(":", 1)[1] for label in labels]
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:
        raise SystemExit(f"{pattern} names {twice} in two packages: an exported entry is named by its target")
    _bazel(checkout, "build", "--noshow_progress", *labels)
    execroot = Path(_bazel(checkout, "info", "execution_root").strip())
    output_base = Path(_bazel(checkout, "info", "output_base").strip())
    files: dict[str, Path] = {}
    for t in targets:
        for short_path, path in t["files"]:
            files[runfiles_path(short_path)] = source(path, checkout, execroot, output_base)
        for path, target in t["symlinks"]:
            files[f"{MAIN}/{path}"] = source(target, checkout, execroot, output_base)
    copy(files, out / "tree")
    build, entries, venvs = [], {}, set()
    for t, name in zip(targets, names, strict=True):
        d = json.loads(source(t["description"], checkout, execroot, output_base).read_text())
        label = "//" + t["label"].split("//", 1)[1]
        #: the bundle's environment: a link here to the store venv its mark resolves to, named by the venv's key, so the
        #: exported description names it by its path in this repository and never by the store's
        venv = Path(os.path.realpath(files[d["bundle"]["venv"]])).parent
        link = out / "venvs" / venv.name
        if not link.is_symlink():
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(venv)
        build.append(exported(name, label, d, "tree", f"venvs/{venv.name}/.complete"))
        cells = [json.loads((out / "tree" / c).read_text())["name"] for c in d["cells"]]
        entries[name] = {"source": label, "kind": d["kind"], "device": bool(d.get("device")), "executor": d["executor"],
                         "params": d["params"], "cells": cells}
        venvs.add(str(venv))
    if commit:
        #: a commit's server has nothing more to build: its memory goes back to the machine now
        _bazel(checkout, "shutdown")
    (out / "exports.BUILD").write_text("\n".join(build))
    (out / "exports.bzl").write_text('"""GENERATED by the bundle tag (fsla_devtools.graph.export): every exported target '
                                     'by name, its kind, executor, parameters and cells."""\n\nENTRIES = '
                                     + starlark(entries) + "\n")
    return {"venvs": sorted(venvs), "entries": len(entries)}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="export.py")
    ap.add_argument("--out", type=Path, required=True, help="the repository's directory")
    ap.add_argument("--cquery", type=Path, required=True, help="the cquery output file (export.cquery)")
    ap.add_argument("--package", action="append", default=[], help="a target pattern whose entries are exported")
    ap.add_argument("--checkout", type=Path, help="a local checkout, built in place")
    ap.add_argument("--commit", help="a commit, checked out under the store and built by its own pins")
    ap.add_argument("--remote", help="the repository the commit is fetched from")
    ap.add_argument("--store", type=Path, help="the machine's graph store")
    a = ap.parse_args(argv)
    if a.commit:
        checkout = check_out(a.store, a.commit, a.remote)
    else:
        checkout = a.checkout.expanduser().resolve()
        if not (checkout / "MODULE.bazel").is_file():
            raise SystemExit(f"{checkout} is no Bazel workspace")
    result = export(checkout, a.out, cquery=a.cquery, packages=a.package, commit=bool(a.commit))
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
