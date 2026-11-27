"""The tripwire's content checks (fsla_devtools.tripwire, over fsla_devtools.mirror's shapes): each check refuses its own
shape on a staged diff and over a pushed range, a clean change passes, and the agent-file rule is the repository's own
setting. The fixtures are joined from parts so this file carries none of the shapes it tests."""
from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from fsla_devtools import mirror, tripwire

AUTHOR = "author" + "@" + "lab.example.org"
OTHER = "someone" + "@" + "mail.test.org"
#: one planted line for each check: (the check's name, the text)
PLANTED = [
    ("a home directory", "path = '" + "/".join(("", "home", "someone", "x")) + "'"),
    ("a personal email address", "contact " + OTHER),
    ("an IPv4 address", "host = " + ".".join(("34", "82", "101", "7"))),
    ("a credential", "token = 'ghp_" + "b" * 36 + "'"),
    ("a credential", "key = 'AK" + "IA" + "A" * 16 + "'"),
    ("a provider account or instance id", "id = 'i-" + "0" * 17 + "'"),
    ("a provider account or instance id", "arn:aws:iam::" + "1" * 12 + ":root"),
    ("a cloud project or bucket", "p = 'project-" + "ab12cd34-ef56'"),
    ("the private scratch repository", "see rola-" + "scratch"),
    ("a ledger event id", "event:" + "2026-11-22-a-card"),
]
#: version numbers of four parts are no addresses; nor are loopback, the author's address and a noreply address
CLEAN = ("version = '1.2.3'\npin = 'tool==13.0.3.0'  # nvcc \u00a74.2.9.1, /modules/x/1.3.1.1/MODULE, \"6.9.7.1-2build\"\n"
         f"local = '127.0.0.1'\nauthor = '{AUTHOR}'\nci = 'noreply@users.noreply.github.com'\n")


def declared(**more) -> dict:
    return {"ships": ["src", "README.md", ".claude", "CLAUDE.md"], "allow": {}, **more}


class Tripwire(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.cwd = os.getcwd()
        self.git("init", "-q", "-b", "main")
        self.write(".github/declarations.json", json.dumps({**declared(), "emails": [AUTHOR]}))
        self.write("README.md", "a repository\n")
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.org", "commit", "-q", "-m", "c")
        os.chdir(self.repo)

    def tearDown(self) -> None:
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def git(self, *args: str) -> None:
        subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)

    def write(self, name: str, text: str) -> None:
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def run_tripwire(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out):
            code = tripwire.main(list(argv))
        return code, out.getvalue()

    def test_each_check_refuses_its_planted_line_when_staged(self):
        for shape, text in PLANTED:
            self.write("src/a.py", "x = 1\n" + text + "\n")
            self.git("add", "src/a.py")
            code, out = self.run_tripwire("--staged")
            self.assertEqual(code, 1, shape)
            self.assertIn(f"src/a.py:2: {shape}", out)
            self.git("rm", "-q", "--cached", "src/a.py")

    def test_a_clean_change_and_the_authors_address_pass(self):
        self.write("src/a.py", CLEAN)
        self.git("add", "src/a.py")
        self.assertEqual(self.run_tripwire("--staged"), (0, ""))

    def test_an_unstaged_line_is_not_seen(self):
        self.write("src/b.py", PLANTED[1][1])
        self.assertEqual(self.run_tripwire("--staged"), (0, ""))

    def test_only_added_lines_count_so_old_history_is_not_raised_again(self):
        self.write("src/a.py", PLANTED[2][1] + "\n")
        self.git("add", "src/a.py")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.org", "commit", "-q", "-m", "old")
        self.write("src/a.py", PLANTED[2][1] + "\nmore = 1\n")
        self.git("add", "src/a.py")
        self.assertEqual(self.run_tripwire("--staged"), (0, ""))

    def test_the_range_form_reads_a_line_added_and_removed_inside_the_range(self):
        base = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        for text in (PLANTED[3][1], "gone = 1"):
            self.write("src/k.py", text + "\n")
            self.git("add", "src/k.py")
            self.git("-c", "user.name=t", "-c", "user.email=t@example.org", "commit", "-q", "-m", "k")
        code, out = self.run_tripwire("--range", f"{base}..HEAD")
        self.assertEqual((code, "src/k.py:1: a credential" in out), (1, True))
        self.assertEqual(self.run_tripwire("--range", "HEAD..HEAD"), (0, ""))

    def test_the_tree_form_reads_whole_files(self):
        self.write("src/a.py", PLANTED[0][1] + "\n")
        self.git("add", "src/a.py")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.org", "commit", "-q", "-m", "c2")
        self.assertEqual(self.run_tripwire("--tree")[0], 1)

    def test_agent_files_are_the_repositorys_own_setting(self):
        for setting, expected in ((True, 1), (False, 0)):
            self.write(".github/declarations.json", json.dumps({**declared(), "agent_files": setting}))
            self.write("CLAUDE.md", "notes\n")
            self.write("sub/.claude/settings.json", "{}\n")
            self.git("add", "-A")
            self.assertEqual(self.run_tripwire("--staged")[0], expected, setting)
            self.git("rm", "-q", "--cached", "-r", "CLAUDE.md", "sub")
        self.assertEqual(mirror.declarations()["agent_files"], False)

    def test_a_scratchpad_file_is_always_refused_and_an_unknown_field_is_not_a_declaration(self):
        self.write(".github/declarations.json", json.dumps({**declared(), "agent_files": False}))
        self.write("src/SCRATCHPAD.md", "n\n")
        self.git("add", "-A")
        self.assertEqual(self.run_tripwire("--staged")[0], 1)
        self.write(".github/declarations.json", json.dumps({**declared(), "agents": False}))
        with self.assertRaisesRegex(SystemExit, "fields must be"):
            mirror.declarations()

    def test_a_shape_a_repository_allows_passes_but_a_credential_never(self):
        decl = {**declared(), "agent_files": True, "emails": [], "allow": {"an IPv4 address": "a documented host"}}
        self.assertEqual(mirror.scan("src/a.py", mirror.whole(PLANTED[2][1]), decl), [])
        self.assertEqual(mirror.scan("src/a.py", mirror.whole(PLANTED[3][1]), decl), ["src/a.py:1: a credential"])


if __name__ == "__main__":
    unittest.main()
