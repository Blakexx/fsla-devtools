"""THE HERMETIC CHECK, every FSLA repository's one lint of its graph (`bazel/hermetic.bzl`'s `hermetic_test`, in each
gate): every tool an action runs is pinned, and the machine gives only what the allowlists name, each entry with why.

    python -m fsla_devtools.hermetic --rows LIST --lock MODULE.bazel.lock --bazelrc .bazelrc --sources LIST
                                     --allowlist F [--allowlist F...] [--cc]

It reads, and refuses:
- THE ACTIONS under the gate's targets, as the aspect wrote them (each action's mnemonic, command line, environment, a
  written file's text and the repositories its inputs come from): a MACHINE PATH anywhere in them (a home directory, the
  store's venvs under it -- `fsla_devtools.results.store`'s pattern, the one the results store refuses in a record),
  which would key the action by the machine it was analysed on, unless a `machine` entry names it exactly (a path of
  an image the graph builds, never of this machine); a MACHINE TOOL, an absolute path under the machine's own
  directories (`/bin`, `/usr`, `/opt`, `/etc`, `/lib`, `/sbin`) no `machine` entry names (a path, or a directory ending
  `/`); and an input from a MACHINE REPOSITORY (`local_config_*`, what Bazel detects on the machine: its C++ toolchain
  above all) no `repositories` entry names.
- THE REPOSITORY RULES, through what Bazel records of them: every module extension's recorded inputs in
  `MODULE.bazel.lock` (`FILE:` and `ENV:` entries), each a file of a module or a variable an `environment` entry names;
  and, since the lockfile records no tool a repository rule runs, the Starlark sources for a `rctx.which("<tool>")` or
  `rctx.execute(["<tool>", ...])` naming a tool by its bare name, which a `tools` entry must name (a pinned tool is
  named by its path, `rctx.path(...)`, and never matches).
- with `--cc`, a `.bazelrc` that lets rules_cc detect the machine's compiler: it keeps
  `--repo_env=BAZEL_DO_NOT_DETECT_CPP_TOOLCHAIN=1`, so the one C++ toolchain is the pinned one.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .results.store import machine_paths

#: an absolute path under the machine's own directories, as a word or inside one (`PATH=/bin:/usr/bin`, `#!/bin/bash`),
#: never a segment of a relative path (`external/gawk+/libawk.a`, `$(dirname X)/bin/python3`)
_MACHINE_TOOL = re.compile(r"(?<![\w.$})}~+/-])/(?:bin|sbin|usr|opt|etc|lib|lib64)(?![\w.+-])(?:/[\w.+-]+)*")
#: a repository Bazel makes by detecting the machine
_MACHINE_REPOSITORY = re.compile(r"^local_config_")
#: a repository rule's machine tool: `rctx.which("git")`, `rctx.execute(["sha256sum", ...])`
_WHICH = re.compile(r"\b(?:rctx|repository_ctx|mctx|module_ctx)\.which\(\s*\"([^\"]+)\"")
_EXECUTE = re.compile(r"\b(?:rctx|repository_ctx|mctx|module_ctx)\.execute\(\s*\[\s*\"([^\"]+)\"")
#: the setting that keeps rules_cc from detecting the machine's compiler
_NO_DETECT = re.compile(r"^\s*(?:common|build)\s+--repo_env=BAZEL_DO_NOT_DETECT_CPP_TOOLCHAIN=1\s*$", re.M)
#: an allowlist's sections, each `{entry: why}`
SECTIONS = ("machine", "repositories", "environment", "tools")


def _allowed(path: str, machine: dict[str, str]) -> bool:
    return path in machine or any(entry.endswith("/") and path.startswith(entry) for entry in machine)


def _repository(canonical: str) -> str:
    """A repository by its own name (`rules_cc++cc_configure_extension+local_config_cc` -> `local_config_cc`)."""
    return canonical.replace("~", "+").rstrip("+").split("+")[-1]


def actions(rows: list[dict], allow: dict) -> list[str]:
    """Every finding over the actions' rows: the mnemonic, the owner, what was found and where."""
    found = []
    for row in rows:
        where = f"{row['mnemonic']} of {row['owner']}"
        texts = [("the command line", " ".join(row.get("argv") or [])),
                 ("the environment", " ".join(f"{k}={v}" for k, v in sorted((row.get("env") or {}).items()))),
                 ("the written file", row.get("content") or "")]
        for part, text in texts:
            for path in machine_paths(text):
                if path not in allow["machine"]:
                    found.append(f"{where}: a machine path in {part}: {path}")
            for path in sorted(set(_MACHINE_TOOL.findall(text))):
                if not _allowed(path, allow["machine"]):
                    found.append(f"{where}: a machine tool in {part}, not in the allowlist: {path}")
        for repo in row.get("repositories") or []:
            name = _repository(repo)
            if _MACHINE_REPOSITORY.match(name) and name not in allow["repositories"]:
                found.append(f"{where}: an input from the machine's repository {name}, not in the allowlist")
    return sorted(set(found))


def lockfile(lock: dict, allow: dict) -> list[str]:
    """Every module extension's recorded `FILE:` and `ENV:` input: a module's file (`@@repo//path`), or a variable the
    allowlist names."""
    found = []
    for extension, platforms in sorted(lock.get("moduleExtensions", {}).items()):
        for entry in (e for p in platforms.values() for e in p.get("recordedInputs", [])):
            kind, _, what = entry.partition(":")
            name = what.split(" ", 1)[0]
            if kind == "FILE" and not name.startswith("@@"):
                found.append(f"{extension}: reads the machine's file {name} (MODULE.bazel.lock)")
            elif kind == "ENV" and name not in allow["environment"]:
                found.append(f"{extension}: reads the machine's variable {name}, not in the allowlist (MODULE.bazel.lock)")
    return found


def sources(files: list[Path], allow: dict) -> list[str]:
    found = []
    for f in files:
        text = f.read_text()
        for pattern in (_WHICH, _EXECUTE):
            for m in pattern.finditer(text):
                if m.group(1) not in allow["tools"]:
                    line = text.count("\n", 0, m.start()) + 1
                    found.append(f"{f}:{line}: a repository rule runs the machine's `{m.group(1)}`, not in the allowlist: "
                                 "fetch it by a pin, or name it there with why the machine gives it")
    return found


def merged(paths: list[Path]) -> dict:
    """The allowlists, the module's default first and then the repository's own, each entry with its reason."""
    allow = {section: {} for section in SECTIONS}
    for path in paths:
        for section, entries in json.loads(path.read_text()).items():
            if section not in allow:
                raise SystemExit(f"{path}: `{section}` is no allowlist section ({', '.join(SECTIONS)})")
            for entry, why in entries.items():
                if not isinstance(why, str) or not why.strip():
                    raise SystemExit(f"{path}: `{section}`'s {entry} states no reason")
                allow[section][entry] = why
    return allow


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rows", type=Path, required=True, help="a file listing the aspect's row files, one a line")
    ap.add_argument("--lock", type=Path, required=True, help="the workspace's MODULE.bazel.lock")
    ap.add_argument("--bazelrc", type=Path, required=True, help="the workspace's .bazelrc")
    ap.add_argument("--sources", type=Path, required=True, help="a file listing the Starlark sources, one a line")
    ap.add_argument("--allowlist", type=Path, action="append", default=[], help="an allowlist, in order")
    ap.add_argument("--cc", action="store_true", help="the workspace compiles C++: its one toolchain is the pinned one")
    a = ap.parse_args(argv)
    allow = merged(a.allowlist)
    rows = [row for listed in a.rows.read_text().split() for row in json.loads(Path(listed).read_text())]
    starlark = [Path(f) for f in a.sources.read_text().split()]
    found = actions(rows, allow) + lockfile(json.loads(a.lock.read_text()), allow) + sources(starlark, allow)
    if a.cc and not _NO_DETECT.search(a.bazelrc.read_text()):
        found.append(".bazelrc: the machine's C++ toolchain is detectable: keep "
                     "`common --repo_env=BAZEL_DO_NOT_DETECT_CPP_TOOLCHAIN=1`, the one C++ toolchain is the pinned one")
    for line in found:
        print(line)
    print(f"hermetic: {len(rows)} actions, {len(starlark)} Starlark sources, the lockfile's extensions; "
          f"{len(found)} finding(s)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
