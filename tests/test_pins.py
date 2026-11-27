"""The shared pins (pins/*.json) are each a complete pin, and the ones that name one release agree on it: Nsight Compute is the
component of the toolkit's manifest, and the toolkit's own component list names no Nsight Compute of its own."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

PINS = Path(__file__).resolve().parents[1] / "pins"
REQUIRED = ("name", "role", "upstream", "version", "asset_sha256", "archive_root", "binary_path")
RELEASES = ("bazelisk", "nsight_compute", "uv")


def pin(name: str) -> dict:
    return json.loads((PINS / f"{name}_pin.json").read_text())


class Pins(unittest.TestCase):
    def test_a_release_pin_names_its_asset_and_digest(self):
        for name in RELEASES:
            with self.subTest(name):
                got = pin(name)
                self.assertEqual(got["name"], name)
                self.assertTrue(got["release_url"].endswith(got["release_asset"]))
                self.assertEqual(len(got["asset_sha256"]), 64)
                self.assertTrue(all(key in got for key in REQUIRED))

    def test_the_toolkit_is_a_manifest_by_its_digest(self):
        toolkit = pin("cuda_toolkit")
        self.assertTrue(toolkit["manifest_url"].endswith(f"/redistrib_{toolkit['version']}.json"))
        self.assertEqual(len(toolkit["manifest_sha256"]), 64)
        self.assertEqual(len(toolkit["components"]), len(set(toolkit["components"])))

    def test_nsight_compute_is_the_toolkits_manifest_release(self):
        self.assertEqual(pin("nsight_compute")["upstream"], pin("cuda_toolkit")["manifest_url"])
        self.assertNotIn("nsight_compute", pin("cuda_toolkit")["components"])


if __name__ == "__main__":
    unittest.main()
