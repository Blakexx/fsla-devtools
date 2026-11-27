"""The bundle tag's export (`fsla_devtools.graph.export`) without a Bazel: a file's runfiles path and its source in the
build, the copy into the repository (and its refusal of a file that would make a package), the generated
`exported_entry` and the Starlark literals it is written in. The fetch itself -- a commit checked out under the store and
built by its own pins, a checkout built in place -- is the bench's desk build. `python -m unittest tests.test_export`"""
from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

from fsla_devtools.graph import export

DESCRIPTION = {"owner": "//measure:solve_c", "kind": "timed", "executor": "measure.provider:timed",
               "params": {"arm": "entmax_solve"}, "binds": ["fsla", "fsla_cu13._C"],
               "cells": ["fsla_devtools++environment+fsla_cells/c.json"],
               "bundle": {"root": "_main", "manifest": "_main/measure/bundle.bundle.json", "python": "/venvs/k/bin/python3"}}


class Export(unittest.TestCase):
    def test_a_file_is_found_by_its_place_in_the_build_and_laid_at_its_runfiles_path(self):
        root, execroot, base = Path("/c"), Path("/x/execroot/_main"), Path("/x")
        self.assertEqual(export.source("bazel-out/k8/bin/m/a.json", root, execroot, base),
                         execroot / "bazel-out/k8/bin/m/a.json")
        self.assertEqual(export.source("external/r+/c.json", root, execroot, base), base / "external/r+/c.json")
        self.assertEqual(export.source("measure/a.py", root, execroot, base), root / "measure/a.py")
        self.assertEqual(export.runfiles_path("../r+/c.json"), "r+/c.json")
        self.assertEqual(export.runfiles_path("measure/a.py"), "_main/measure/a.py")

    def test_the_copy_is_a_copy_and_a_file_that_would_make_a_package_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "a.py"
            src.write_text("x = 1\n")
            export.copy({"_main/m/a.py": src}, Path(tmp) / "tree")
            copied = Path(tmp) / "tree" / "_main" / "m" / "a.py"
            self.assertFalse(copied.is_symlink())
            self.assertEqual(copied.read_text(), "x = 1\n")
            with self.assertRaisesRegex(SystemExit, "a package of its own"):
                export.copy({"_main/m/BUILD.bazel": src}, Path(tmp) / "tree2")

    def test_an_exported_entry_names_its_paths_in_the_repository_in_starlark(self):
        text = export.exported("solve_c", "//measure:solve_c", DESCRIPTION, "tree")
        call = ast.parse(text).body[0].value  #: a Starlark call of plain literals is a Python one
        fields = {k.arg: ast.literal_eval(k.value) for k in call.keywords}
        self.assertEqual(fields["root"], "tree/_main")
        self.assertEqual(fields["manifest"], "tree/_main/measure/bundle.bundle.json")
        self.assertEqual(fields["cells"], ["tree/fsla_devtools++environment+fsla_cells/c.json"])
        self.assertEqual(fields["params"], '{"arm": "entmax_solve"}')
        self.assertIs(fields["device"], False)
        self.assertEqual(export.starlark({"b": [True, None], "a": "true"}), '{"a": "true", "b": [True, None]}')


if __name__ == "__main__":
    unittest.main()
