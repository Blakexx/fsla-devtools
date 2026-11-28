"""The host C++ toolchain's root (`fsla_devtools.graph.rpm_root`) on a hand-made RPM: its payload read through the lead and
both headers, a file (its execute bit kept), a link to an absolute path made a link within the root, a hard link group
written to every name, the documentation skipped, `/lib64` merged into `/usr`, and a path that leaves the root refused.
The real RPMs are the `host_toolchain` tag's fetch. `python -m unittest tests.test_rpm_root`"""
from __future__ import annotations

import lzma
import os
import struct
import tempfile
import unittest
from pathlib import Path

from fsla_devtools.graph import rpm_root


def _cpio(entries: list[tuple[str, int, int, int, bytes]]) -> bytes:
    """A newc archive of (name, mode, ino, nlink, data), with its trailer."""
    out = b""
    for name, mode, ino, nlink, data in entries + [("TRAILER!!!", 0, 0, 1, b"")]:
        raw = name.encode() + b"\0"
        fields = [ino, mode, 0, 0, nlink, 0, len(data), 0, 0, 0, 0, len(raw), 0]
        head = b"070701" + b"".join(b"%08X" % f for f in fields)
        out += head + raw + b"\0" * (-(len(head) + len(raw)) % 4) + data + b"\0" * (-len(data) % 4)
    return out


def _header(tags: dict[int, bytes]) -> bytes:
    index, store = b"", b""
    for tag, data in tags.items():
        index += struct.pack(">IIII", tag, 6, len(store), 1)
        store += data
    return b"\x8e\xad\xe8\x01\0\0\0\0" + struct.pack(">II", len(tags), len(store)) + index + store


def _rpm(entries) -> bytes:
    lead = b"\xed\xab\xee\xdb" + b"\0" * 92
    signature = _header({})
    signature += b"\0" * (-len(signature) % 8)
    return lead + signature + _header({1125: b"xz\0"}) + lzma.compress(_cpio(entries))


class RpmRoot(unittest.TestCase):
    def test_a_payload_is_laid_out_under_the_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            rpm, root = Path(tmp) / "a.rpm", Path(tmp) / "root"
            rpm.write_bytes(_rpm([
                ("./usr/bin/tool", 0o100755, 1, 1, b"#!x\n"),
                ("./lib64/libz.so.1.2", 0o100644, 2, 1, b"z"),
                ("./lib64/libz.so.1", 0o120777, 3, 1, b"libz.so.1.2"),
                ("./usr/bin/alt", 0o120777, 4, 1, b"/usr/bin/tool"),
                ("./usr/bin/one", 0o100755, 5, 2, b""),
                ("./usr/bin/two", 0o100755, 5, 2, b"same"),
                ("./usr/share/doc/a/README", 0o100644, 6, 1, b"doc"),
            ]))
            self.assertEqual(rpm_root.main(["--out", str(root), str(rpm)]), 0)
            self.assertEqual((root / "usr/bin/tool").read_bytes(), b"#!x\n")
            self.assertTrue(os.access(root / "usr/bin/tool", os.X_OK))
            #: an absolute link is a link within the root
            self.assertEqual(os.readlink(root / "usr/bin/alt"), "tool")
            self.assertEqual((root / "usr/bin/one").read_bytes(), b"same")
            self.assertFalse((root / "usr/share/doc").exists())
            #: /lib64 is a link into /usr, its files and their links moved there
            self.assertEqual(os.readlink(root / "lib64"), "usr/lib64")
            self.assertEqual((root / "lib64/libz.so.1").read_bytes(), b"z")
            self.assertEqual((root / "usr/lib64/libz.so.1").read_bytes(), b"z")

    def test_a_path_that_leaves_the_root_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            rpm = Path(tmp) / "a.rpm"
            rpm.write_bytes(_rpm([("../escape", 0o100644, 1, 1, b"x")]))
            with self.assertRaisesRegex(SystemExit, "leaves the root"):
                rpm_root.unpack(rpm, Path(tmp) / "root")


if __name__ == "__main__":
    unittest.main()
