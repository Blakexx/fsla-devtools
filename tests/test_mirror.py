"""The declarations (fsla_devtools.mirror), in a throwaway git repository: every tracked path is declared, the longest
declaration wins, and the tripwire's shapes refuse agent files and private-shaped strings, including in the tool's own
text."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from fsla_devtools import mirror

#: a home-directory path, joined so this file carries none; the fixtures below split their other private shapes the same
#: way, so the file passes the tripwire it tests
SOMEONES_HOME = "/".join(("", "home", "someone"))
DECLARED = {"public": "owner/repo", "ships": [".github", "README.md", "src"],
            "private": ["docs/drafts"], "allow": {}}


class Declarations(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        (self.repo / "src").mkdir(parents=True)
        (self.repo / ".github" / "mirror").mkdir(parents=True)
        (self.repo / "README.md").write_text("a repository\n")
        (self.repo / "src" / "code.py").write_text("print('hi')\n")
        (self.repo / ".github" / "mirror" / "declarations.json").write_text(json.dumps(DECLARED))
        self.git("init", "-q", "-b", "main")
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.org", "commit", "-q", "-m", "c")
        self.cwd = os.getcwd()
        os.chdir(self.repo)

    def tearDown(self) -> None:
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def git(self, *args: str) -> None:
        subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)

    def test_the_longest_declaration_wins(self):
        self.assertEqual(mirror.declaration(".github/mirror/declarations.json", DECLARED), "ships")
        self.assertEqual(mirror.declaration("docs/drafts/a.md", DECLARED), "private")
        self.assertEqual(mirror.declaration("src/code.py", DECLARED), "ships")
        self.assertIsNone(mirror.declaration("srcx/code.py", DECLARED))

    def test_an_undeclared_tracked_path_refuses(self):
        (self.repo / "notes.txt").write_text("n\n")
        self.git("add", "notes.txt")
        self.assertEqual(mirror.main(["--check"]), 1)

    def test_the_tripwire_refuses_private_shaped_files_but_not_its_own_text(self):
        decl = {**DECLARED, "agent_files": True, "emails": [], "private": []}
        own = {"tool/mirror.py": mirror.whole(Path(mirror.__file__).read_text())}
        self.assertEqual(mirror.scan_files(own, decl), [])
        for name, text, finding in [("csrc/CLAUDE.md", "notes", "an agent file"),
                                    ("docs/a/.claude/settings.json", "{}", "an agent file"),
                                    ("docs/x.md", f"built at {SOMEONES_HOME}/fsla", "a home directory"),
                                    ("docs/y.md", "contact someone" + "@gmail.com", "a personal email address"),
                                    ("tools/x.py", "token = 'ghp_" + "a" * 36 + "'", "a credential")]:
            found = mirror.scan_files({name: mirror.whole(text)}, decl)
            self.assertTrue(any(f.startswith(name) and f.endswith(finding) for f in found), (name, found))

    def test_an_allowed_shape_passes_but_a_credential_can_never_be_allowed(self):
        scan = Path(self.tmp.name) / "scan"
        scan.mkdir()
        record = {"r.json": mirror.whole(f'{{"fsla_file": "{SOMEONES_HOME}/fsla/__init__.py"}}')}
        self.assertEqual(mirror.scan_files(record, {**DECLARED, "private": [], "agent_files": True, "emails": [],
                                                    "allow": ["a home directory"]}), [])
        bad = scan / "declarations.json"
        bad.write_text('{"public": "o/r", "ships": [], "private": [], "allow": {"a credential": "no"}}')
        with self.assertRaisesRegex(SystemExit, "no allowable shape"):
            mirror.declarations(bad)


if __name__ == "__main__":
    unittest.main()
