"""THE CONTENT CHECKS, AT COMMIT AND PUSH: what a public repository must not carry (home paths, other people's
email addresses, IPv4 addresses, credential formats, provider account and instance ids, the private scratch repository's
name, ledger event ids, and the agent files where a repository's declarations refuse them), run over the lines a
commit or a push adds, so a violation is refused before it is history.

    python -m fsla_devtools.tripwire --staged           the staged diff (a pre-commit hook)
    python -m fsla_devtools.tripwire --range A..B ...   every commit of the revisions, patch by patch (a pre-push hook);
                                                        more than one argument is passed to `git log` as given
    python -m fsla_devtools.tripwire --tree             every tracked file of HEAD, whole

Run from inside the repository, under its `.github/declarations.json`: `allow`, `emails` and `agent_files` are the
repository's own. Exit 1 on any refusal, each a line `path:line: shape`.
"""
from __future__ import annotations

import sys

from fsla_devtools import mirror


def check(argv: list[str]) -> list[str]:
    decl = mirror.declarations()
    if argv == ["--staged"]:
        return mirror.scan_diff(mirror.staged_diff(), decl)
    if len(argv) >= 2 and argv[0] == "--range":
        return mirror.scan_diff(mirror.range_diff(argv[1:]), decl)
    if argv == ["--tree"]:
        paths = [p for p in mirror._git("ls-tree", "-r", "-z", "--name-only", "HEAD").decode().split("\0") if p]
        files = {p: mirror.whole(mirror._git("show", f"HEAD:{p}").decode(errors="ignore")) for p in paths}
        return mirror.scan_files(files, decl)
    raise SystemExit(__doc__)


def main(argv: list[str]) -> int:
    refusals = check(argv)
    for refusal in refusals:
        print(f"TRIPWIRE REFUSED: {refusal} ({mirror.DECLARATIONS})")
    return 1 if refusals else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
