"""AN INSTRUMENT RUN: a tool over the files it reads, laid out as a tree, its JSON the instrument's output.

    python -m fsla_devtools.instrument --files LIST --tool PATH --out FILE [--device] -- ARGS...

`bazel/measure.bzl`'s `instrument` rule names a tool, its arguments and the files it reads; this module copies each
file to its path in a fresh tree (links followed, so a tool resolving its own path finds the tree and never the checkout
the files link into), and runs the tool there on the environment's interpreter with the tree as its import path:
`TOOL ARGS... --json OUT`. The tool's contract: it writes its JSON to `OUT` and exits 0; a tool that cannot run on what it
was handed (a binary that lacks what it reads, a host without the device it needs) writes `{"refused": WHY}` and exits 0,
so the refusal is the instrument's recorded output and stops nothing else; a tool that crashes, or exits 0 without its
JSON, fails. The rule's action runs it for an instrument without a device, its output a build output; an instrument on
the device runs in the run phase of the store target that records it (`fsla_devtools.record`), since a build phase holds
the host budget and the GPU taken under it deadlocks against a measurement, which takes the GPU first. An instrument
without a device sees none, and no machine's dev config reaches it (its home is the tree's own); a device instrument
runs in the run's environment and takes the machine's locks itself.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

#: the key of a refusal: `{"refused": WHY}` is an instrument's output when it cannot run
REFUSED = "refused"


#: AN ENVIRONMENT'S COMPLETION MARK (an environment repository's `venv/.complete`, bazel/rules.bzl's `environment`): laid out
#: as its directory, a link to the store venv it resolves to, so a description in the tree resolves to its interpreter
#: (`fsla_devtools.timing.interpreter`); a copy of the mark would resolve to the tree
MARK = ("venv", ".complete")


def lay_out(files: list[tuple[str, str]], tree: Path) -> None:
    """Each `(path in the tree, source)` copied to its path under `tree`, links followed; an environment's mark as the
    link to its venv (`MARK`)."""
    for path, source in files:
        dest = tree / path
        if (dest.parent.name, dest.name) == MARK:
            if not dest.parent.is_symlink():
                dest.parent.parent.mkdir(parents=True, exist_ok=True)
                dest.parent.symlink_to(Path(os.path.realpath(source)).parent)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        shutil.copymode(source, dest)


def run(files: list[tuple[str, str]], tool: str, args: list[str], out: Path, *, python: str, device: bool) -> dict:
    """The tool run over `files` in a fresh tree, its JSON written to `out` and returned."""
    out = out.absolute()
    out.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="instrument-") as scratch:
        tree = Path(scratch) / "tree"
        lay_out(files, tree)
        env = dict(os.environ) if device else {"HOME": str(Path(scratch) / "home"), "CUDA_VISIBLE_DEVICES": ""}
        env.update(PYTHONPATH=str(tree), PYTHONDONTWRITEBYTECODE="1",
                   PATH=os.pathsep.join([str(Path(python).parent), "/usr/bin", "/bin"]))
        done = subprocess.run([python, tool, *args, "--json", str(out)], cwd=tree, env=env, check=False)
    if done.returncode:
        raise SystemExit(f"instrument: {tool} exited {done.returncode}")
    if not out.is_file():
        raise SystemExit(f"instrument: {tool} exited 0 and wrote no JSON to the path after --json")
    return json.loads(out.read_text())


def read_files(listing: Path) -> list[tuple[str, str]]:
    """A file list: alternate lines, a path in the tree and its source."""
    lines = listing.read_text().splitlines()
    return list(zip(lines[0::2], lines[1::2], strict=True))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m fsla_devtools.instrument")
    ap.add_argument("--files", type=Path, required=True, help="the file list: a path in the tree, then its source")
    ap.add_argument("--tool", required=True, help="the tool's path in the tree")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--device", action="store_true", help="the tool sees the machine's devices")
    ap.add_argument("args", nargs="*", help="the tool's arguments, after --")
    a = ap.parse_args(argv)
    output = run(read_files(a.files), a.tool, a.args, a.out, python=sys.executable, device=a.device)
    if isinstance(output, dict) and REFUSED in output:
        print(f"instrument: {a.tool} refused: {output[REFUSED]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))


__all__ = ["REFUSED", "lay_out", "main", "read_files", "run"]
