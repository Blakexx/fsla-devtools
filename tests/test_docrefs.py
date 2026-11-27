"""The doc references (fsla_devtools.docrefs), in a throwaway git repository: what a reference is, what its target's
digest moves on, the gate's verdict, and the repository's configuration (its package roots, mirror rules, excluded docs
and pinned data)."""
from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from fsla_devtools import docrefs

#: the configuration the tests run under, as a repository's `docrefs_test` hands it
FLAGS = ["--lock", "docs/references.lock.json", "--package", "fsla=", "--package", "tools=", "--package",
         "bench=measure", "--mirror", "docs/internals/tools/=tools/", "--mirror", "docs/internals/=csrc/src/",
         "--complete", "docs/internals/", "--exclude", "docs/LEDGER.md"]
CODE_V1 = "def f(x):\n    return x + 1\n"
DOC = "# Page\n\nThe function adds one: `fsla/a.py`'s `f`.\n"


class Tree(unittest.TestCase):
    """A git repository the tool is configured on, ``plant`` adding tracked files to it."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        self.data = Path(self.tmp.name) / "data"
        (self.data / "lab").mkdir(parents=True)
        (self.data / "BUILD.bazel").write_text("")
        (self.data / "lab" / "x.json").write_text("{}\n")
        self.cwd = os.getcwd()
        os.chdir(self.root)
        self.git("init", "-q", "-b", "main")
        docrefs.configure([*FLAGS, "--data", str(self.data / "BUILD.bazel")])
        self.plant({"README.md": "a repository\n"})
        self.commit()

    def tearDown(self) -> None:
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True, text=True).stdout

    def commit(self) -> None:
        self.git("-c", "user.name=t", "-c", "user.email=t@example.org", "commit", "-q", "-m", "c", "--allow-empty")

    def plant(self, files: dict[str, str]) -> set[str]:
        for name, text in files.items():
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text(text)
        self.git("add", "-A")
        docrefs._text.cache_clear()  #: the tool reads each file once per run; a test rewrites them within one
        return docrefs.tracked()

    def run_tool(self, *args: str) -> tuple[int, str]:
        """The tool's command line, as `bazel run //:docrefs -- ARGS` gives it under the repository's configuration."""
        docrefs._text.cache_clear()
        out = io.StringIO()
        with redirect_stdout(out):
            code = docrefs.main([*args, *FLAGS, "--data", str(self.data / "BUILD.bazel")])
        return code, out.getvalue()

    def refs(self, doc: str, files: set[str], pinned: frozenset[str] = frozenset()) -> set[str]:
        return {str(t) for t in docrefs.references(doc, files, pinned)}

    def read(self, spelled: str, files: set[str]) -> str | None:
        return docrefs.read(docrefs.Target.spelled(spelled), files)


class Targets(Tree):
    def test_a_docstring_a_comment_or_a_reformat_moves_no_pin_and_a_code_change_does(self):
        files = self.plant({"fsla/m.py": 'def f(x):\n    """Old words."""\n    return x + 1\n'})
        before = self.read("fsla/m.py::f", files)
        self.plant({"fsla/m.py": 'def f(x):  # a comment\n    """New words."""\n\n    return (x + 1)\n'})
        self.assertEqual(self.read("fsla/m.py::f", files), before)
        self.plant({"fsla/m.py": "def f(x):\n    return x + 2\n"})
        self.assertNotIn(self.read("fsla/m.py::f", files), (before, None))

    def test_a_method_a_constant_and_a_re_export_resolve_and_a_missing_name_does_not(self):
        files = self.plant({"fsla/__init__.py": "", "fsla/b.py": "from fsla.a import C\n",
                            "fsla/a.py": "LIMIT = 3\n\nclass C:\n    def go(self):\n        return 1\n"})
        self.assertTrue(self.read("fsla/a.py::LIMIT", files))
        self.assertEqual(self.read("fsla/b.py::C.go", files), self.read("fsla/a.py::C.go", files))
        self.assertIsNone(self.read("fsla/a.py::C.stop", files))

    def test_a_section_runs_to_the_next_heading_at_its_level(self):
        files = self.plant({"docs/p.md": "# Top\n\n## Wheels\n\none\n\n### Detail\n\ntwo\n\n## Next\n\nthree\n"})
        wheels = self.read("docs/p.md#wheels", files)
        self.plant({"docs/p.md": "# Top\n\n## Wheels\n\none\n\n### Detail\n\ntwo\n\n## Next\n\nchanged\n"})
        self.assertEqual(self.read("docs/p.md#wheels", files), wheels)
        self.plant({"docs/p.md": "# Top\n\n## Wheels\n\none\n\n### Detail\n\nchanged\n\n## Next\n\nchanged\n"})
        self.assertNotEqual(self.read("docs/p.md#wheels", files), wheels)

    def test_device_code_pins_a_function_body_or_a_named_region(self):
        files = self.plant({"csrc/k.cu": ("static int step(int x) {\n  // why\n  return x + 1;\n}\n"
                                          "//: @ref fold\nint y = 2;\n//: @end\n")})
        body, region = self.read("csrc/k.cu::step", files), self.read("csrc/k.cu::fold", files)
        self.plant({"csrc/k.cu": ("static int step(int x) {\n  return x + 1;  /* reworded */\n}\n"
                                  "//: @ref fold\nint y = 3;\n//: @end\n")})
        self.assertEqual(self.read("csrc/k.cu::step", files), body)
        self.assertNotEqual(self.read("csrc/k.cu::fold", files), region)

    def test_a_lock_rows_spelling_reads_back_as_its_target(self):
        for target in (docrefs.Target("fsla/a.py", "C.go"), docrefs.Target("docs/p.md", "wheels"),
                       docrefs.Target("docs/p.md"), docrefs.Target("data:lab/x.json")):
            self.assertEqual(docrefs.Target.spelled(str(target)), target)


class References(Tree):
    def test_the_citation_forms_are_read_and_a_fenced_block_is_not(self):
        files = self.plant({
            "fsla/__init__.py": "", "fsla/ops.py": "def carry():\n    pass\n", "tools/t.py": "def main():\n    pass\n",
            "docs/other.md": "# Title\n\n## Wheels\n\ntext\n",
            "docs/page.md": ("`tools/t.py`'s `main` and `fsla.ops.carry` beside `docs/other.md` and [w](other.md#wheels)\n"
                             "and `data:lab/x.json`\n```\n`fsla.ops.fenced`\n```\n`torch.ops.x` and `a phrase`\n"),
        })
        self.assertEqual(self.refs("docs/page.md", files), {"tools/t.py::main", "fsla/ops.py::carry", "docs/other.md",
                                                             "docs/other.md#wheels", "data:lab/x.json"})

    def test_a_package_root_resolves_under_the_directory_it_imports_from(self):
        files = self.plant({"measure/bench/__init__.py": "", "measure/bench/run.py": "def go():\n    pass\n",
                            "docs/page.md": "`bench.run.go` runs it; `measure.bench` is no root.\n"})
        self.assertEqual(self.refs("docs/page.md", files), {"measure/bench/run.py::go"})

    def test_a_mirror_page_cites_its_sources_names_bare_and_keeps_a_pinned_one_that_left(self):
        files = self.plant({"tools/t.py": "def main():\n    pass\n",
                            "docs/internals/tools/t.md": "# `tools/t.py`\n\n`main` calls `helper`, and `gone` went.\n"})
        self.assertEqual(self.refs("docs/internals/tools/t.md", files), {"tools/t.py", "tools/t.py::main"})
        kept = docrefs.references("docs/internals/tools/t.md", files, pinned=frozenset({"tools/t.py::gone"}))
        self.assertIn(docrefs.Target("tools/t.py", "gone"), kept)
        self.assertIsNone(self.read("tools/t.py::gone", files))

    def test_every_cpp_source_has_a_page_and_a_page_names_only_live_identifiers(self):
        files = self.plant({"csrc/src/a/k.cu": "void kern() {}\n", "csrc/src/a/k.cuh": "int live_name;\n",
                            "csrc/src/b/m.cuh": "int other;\n", "csrc/third_party/x/y.cuh": "int z;\n",
                            "tools/t.py": "def tool_name(): pass\n"})
        self.assertEqual(docrefs.mirror_page("csrc/src/a/k.cuh"), "docs/internals/a/k.md")
        self.assertEqual(docrefs.mirror_findings(files)[0].split(" has no")[0], "csrc/src/a/k.cu")
        self.assertEqual(len(docrefs.mirror_findings(files)), 3)
        files = self.plant({"docs/internals/a/k.md": "`kern`, `live_name`, `tool_name` live; `gone_name` is not.\n",
                            "docs/internals/b/m.md": "<!-- ABSENT: retired_ring other -->\nIt names `retired_ring`.\n"})
        found = docrefs.mirror_findings(files)
        self.assertEqual(len(found), 2)
        self.assertIn("docs/internals/a/k.md:1: names `gone_name`", found[0])
        self.assertIn("declares `other` ABSENT, and the tree carries it", found[1])

    def test_an_excluded_doc_is_never_read(self):
        files = self.plant({"docs/LEDGER.md": "`fsla/gone.py` was deleted\n", "docs/p.md": "see `fsla/gone.py`\n"})
        self.assertEqual(docrefs.docs(files), ["README.md", "docs/p.md"])

    def test_a_data_citation_is_checked_to_exist_in_the_pinned_data(self):
        files = self.plant({})
        self.assertEqual(self.read("data:lab/x.json", files), "")
        self.assertEqual(self.read("data:lab", files), "")
        self.assertIsNone(self.read("data:lab/y.json", files))


class Verdict(unittest.TestCase):
    def test_the_verdict_names_what_is_new_stale_broken_unread_and_pending(self):
        target = docrefs.Target("fsla/a.py", "f")
        pin = {"doc": "docs/p.md", "target": str(target), "sha256": "aaaa", "pinned_at": "0", "reviewed": True}
        lock = {("docs/p.md", str(target)): pin,
                ("docs/p.md", "fsla/gone.py"): {**pin, "target": "fsla/gone.py"},
                ("docs/p.md", "fsla/broken.py"): {**pin, "target": "fsla/broken.py", "sha256": None},
                ("docs/p.md", "fsla/unread.py"): {**pin, "target": "fsla/unread.py", "reviewed": False}}
        found = {("docs/p.md", str(target)): (target, 3, "bbbb"),
                 ("docs/p.md", "fsla/broken.py"): (docrefs.Target("fsla/broken.py"), 4, None),
                 ("docs/p.md", "fsla/unread.py"): (docrefs.Target("fsla/unread.py"), 5, "aaaa"),
                 ("docs/q.md", str(target)): (target, 1, "bbbb")}
        new, stale, broken, unread, pending = docrefs.verdict(found, lock)
        self.assertEqual(set(new), {("docs/q.md", str(target))})
        self.assertEqual(stale, [("docs/p.md", "fsla/gone.py")])
        self.assertEqual(broken, [("docs/p.md", "fsla/broken.py")])
        self.assertEqual(unread, [("docs/p.md", "fsla/unread.py")])
        self.assertEqual(set(pending), {("docs/p.md", str(target))})


class Gate(Tree):
    """THE GATE: a doc is clean only when every reference in it is pinned, read and current."""

    def setUp(self) -> None:
        super().setUp()
        self.plant({"fsla/a.py": CODE_V1, "docs/p.md": DOC})
        self.commit()

    def test_an_unread_reference_makes_the_gate_red_and_accepting_the_whole_doc_makes_it_green(self):
        self.assertEqual(self.run_tool("init")[0], 0)
        code, out = self.run_tool()
        self.assertEqual(code, 1)
        self.assertIn("docs/p.md:3: `fsla/a.py::f` was never read", out)
        self.assertEqual(self.run_tool("accept", "--whole", "docs/p.md")[0], 0)
        self.assertEqual(self.run_tool()[0], 0)

    def test_the_lock_is_one_file_at_the_configured_path_naming_its_interpreter(self):
        self.run_tool("init")
        lock = json.loads((self.root / "docs" / "references.lock.json").read_text())
        self.assertEqual((lock["python"], [r["target"] for r in lock["references"]]), (docrefs.PYTHON, ["fsla/a.py::f"]))

    def test_a_changed_target_makes_the_gate_red_naming_the_doc_and_the_command_and_an_accept_makes_it_green(self):
        self.run_tool("init")
        self.run_tool("accept", "--whole", "docs/p.md")
        self.assertEqual(self.run_tool()[0], 0)
        self.plant({"fsla/a.py": "def f(x):\n    return x + 2\n"})
        code, out = self.run_tool()
        self.assertEqual(code, 1)
        self.assertIn("docs/p.md:3: `fsla/a.py::f` changed since its pin", out)
        self.assertIn("bazel run //:docrefs -- accept docs/p.md", out)
        code, out = self.run_tool("pending")
        self.assertIn("fsla/a.py::f: CHANGED since", out)
        self.assertIn("+    return x + 2", out)
        self.assertEqual(self.run_tool("accept", "docs/p.md")[0], 0)
        self.assertEqual(self.run_tool()[0], 0)

    def test_a_comment_in_the_target_leaves_the_gate_green(self):
        self.run_tool("init")
        self.run_tool("accept", "--whole", "docs/p.md")
        self.plant({"fsla/a.py": "def f(x):  # reworded\n    return x + 1\n"})
        self.assertEqual(self.run_tool()[0], 0)

    def test_a_vanished_target_is_red_and_a_read_accept_drops_a_bare_name_but_a_path_form_stays_red(self):
        self.plant({"tools/t.py": "def helper():\n    pass\n",
                    "docs/internals/tools/t.md": "# `tools/t.py`\n\n`helper` is called.\n"})
        self.run_tool("init")
        self.run_tool("accept", "--whole", "docs/p.md", "docs/internals/tools/t.md")
        self.assertEqual(self.run_tool()[0], 0)
        self.plant({"tools/t.py": "def other():\n    pass\n", "fsla/a.py": "def g(x):\n    return x\n"})
        code, out = self.run_tool()
        self.assertEqual(code, 1)
        self.assertIn("docs/internals/tools/t.md:3: `tools/t.py::helper` vanished since its pin", out)
        self.assertIn("docs/p.md:3: `fsla/a.py::f` vanished since its pin", out)
        self.assertIn("`tools/t.py::helper` names nothing now",
                      self.run_tool("accept", "docs/p.md", "docs/internals/tools/t.md")[1])
        code, out = self.run_tool()
        self.assertEqual(code, 1)  #: the possessive names a function that is gone: it resolves to nothing
        self.assertIn("docs/p.md:3: the reference to `fsla/a.py::f` resolves to nothing", out)

    def test_a_moved_file_carries_its_citations_and_pins_and_a_changed_one_is_refused(self):
        self.run_tool("init")
        self.run_tool("accept", "--whole", "docs/p.md")
        self.git("mv", "fsla/a.py", "fsla/b.py")
        self.assertEqual(self.run_tool()[0], 1)
        self.plant({"fsla/b.py": "def f(x):\n    return x + 2\n"})
        code, out = self.run_tool("move", "fsla/a.py", "fsla/b.py")
        self.assertEqual(code, 1)
        self.assertIn("not the same content at the new path", out)
        self.assertEqual((self.root / "docs" / "p.md").read_text(), DOC)
        self.plant({"fsla/b.py": CODE_V1})
        self.assertEqual(self.run_tool("move", "fsla/a.py", "fsla/b.py")[0], 0)
        self.assertIn("`fsla/b.py`'s `f`", (self.root / "docs" / "p.md").read_text())
        self.assertEqual(self.run_tool()[0], 0)

    def test_a_lock_pinned_under_another_interpreter_is_refused(self):
        self.run_tool("init")
        lock = self.root / "docs" / "references.lock.json"
        lock.write_text(lock.read_text().replace(f'"python": "{docrefs.PYTHON}"', '"python": "2.7"'))
        with self.assertRaisesRegex(SystemExit, "pinned under Python 2.7"):
            self.run_tool()


if __name__ == "__main__":
    unittest.main()
