"""The hermetic check (`fsla_devtools.hermetic`) fires on each thing it refuses and passes what the allowlist names:
a machine path, a machine tool, the machine's detected repository, a machine file or variable a module extension
recorded, a repository rule's machine tool, and a detectable C++ toolchain. `python -m unittest tests.test_hermetic`"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fsla_devtools import hermetic

#: a home directory, spelled so this file carries none (the tripwire reads it)
HOME = "/" + "home" + "/someone"
ALLOW = {"machine": {"/bin/bash": "the shell", "/usr/bin": "the path"}, "repositories": {}, "environment": {"FSLA_OK": "ok"},
         "tools": {"git": "the index"}}


def row(**fields) -> dict:
    return {"owner": "//:t", "mnemonic": "Action", "argv": [], "env": {}, **fields}


class Hermetic(unittest.TestCase):
    def test_a_machine_path_anywhere_in_an_action_is_refused(self):
        for found in (hermetic.actions([row(argv=[f"{HOME}/.cache/store/venvs/k/bin/python3", "x.py"])], ALLOW),
                      hermetic.actions([row(env={"PATH": f"{HOME}/bin"})], ALLOW),
                      hermetic.actions([row(mnemonic="FileWrite", content=f"export PATH={HOME}/venv/bin\n")], ALLOW)):
            self.assertEqual(len(found), 1, found)
            self.assertIn("a machine path", found[0])

    def test_a_machine_tool_is_refused_unless_the_allowlist_names_it(self):
        self.assertEqual(hermetic.actions([row(argv=["/bin/bash", "-c", "x"], env={"PATH": "/usr/bin"})], ALLOW), [])
        found = hermetic.actions([row(argv=["/usr/local/bin/gcc", "-c", "a.c"])], ALLOW)
        self.assertEqual(found, ["Action of //:t: a machine tool in the command line, not in the allowlist: "
                                 "/usr/local/bin/gcc"])

    def test_an_input_from_the_machine_s_detected_toolchain_is_refused(self):
        found = hermetic.actions([row(repositories=["rules_cc++cc_configure_extension+local_config_cc", "warpline+"])], ALLOW)
        self.assertEqual(found, ["Action of //:t: an input from the machine's repository local_config_cc, "
                                 "not in the allowlist"])

    def test_the_lockfile_s_machine_file_and_unnamed_variable_are_refused(self):
        lock = {"moduleExtensions": {"@@x+//:e.bzl%e": {"general": {"recordedInputs": [
            "FILE:@@x+//MODULE.bazel abc", "ENV:FSLA_OK 1", "ENV:HOME /x", "FILE:/etc/os-release def",
            "REPO_MAPPING:x+,y y+"]}}}}
        found = hermetic.lockfile(lock, ALLOW)
        self.assertEqual(len(found), 2, found)
        self.assertTrue(any("variable HOME" in f for f in found) and any("file /etc/os-release" in f for f in found))

    def test_a_repository_rule_s_machine_tool_is_refused_unless_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            bzl = Path(tmp) / "r.bzl"
            bzl.write_text('def _impl(rctx):\n    git = rctx.which("git")\n    rctx.execute(["sha256sum", "f"])\n'
                           '    cc = rctx.which("gcc")\n    rctx.execute([rctx.path(rctx.attr.uv), "x"])\n')
            found = hermetic.sources([bzl], ALLOW)
        self.assertEqual(len(found), 2, found)
        self.assertTrue(any("`sha256sum`" in f for f in found) and any("`gcc`" in f for f in found))

    def test_an_allowlist_entry_states_its_reason_and_the_two_merge_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp) / "a.json", Path(tmp) / "b.json"
            first.write_text(json.dumps({"machine": {"/bin/bash": "the shell"}}))
            second.write_text(json.dumps({"tools": {"git": "the index"}}))
            allow = hermetic.merged([first, second])
            self.assertEqual((allow["machine"], allow["tools"]), ({"/bin/bash": "the shell"}, {"git": "the index"}))
            second.write_text(json.dumps({"tools": {"git": " "}}))
            with self.assertRaisesRegex(SystemExit, "states no reason"):
                hermetic.merged([first, second])


if __name__ == "__main__":
    unittest.main()
