"""THE HOST C++ TOOLCHAIN'S ROOT: RPMs unpacked into one directory, as the package manager would lay them out under `/`
(bazel/environment.bzl's `host_toolchain` tag runs this on the interpreter it is handed, standard library only; the RPMs are
fetched and checked against the pin's digests before it runs).

    python3 rpm_root.py --out ROOT RPM...

An RPM is a lead, a signature header, a header and a compressed cpio payload ("newc"); the header names the payload's
compressor. Each payload's directories, files (their mode's execute bits kept) and symbolic links are written under
ROOT; a hard link's data is its group's, written to every name; a link to an absolute path becomes a link within ROOT, as
if ROOT were `/`. A path that would leave ROOT is refused, and the documentation, manual pages, locales and build-id
links are not written. Nothing an RPM's scripts would do is done: the root is the files, never an installed system.
"""
from __future__ import annotations

import argparse
import io
import lzma
import os
import re
import shutil
import stat
import struct
import sys
from pathlib import Path

_HEADER_MAGIC = b"\x8e\xad\xe8\x01"
#: the header tag naming the payload's compressor (`xz`, `lzma`; AlmaLinux 8's are xz)
_PAYLOADCOMPRESSOR = 1125
_STRING = 6
#: what no build reads: the documentation, the manual pages, the locales and the debug files' build-id links
_SKIPPED = re.compile(r"^\.?/?usr/share/(doc|man|info|licenses|locale)/|/share/(doc|man|info|locale)/|/\.build-id/")


def _header(blob: bytes, at: int) -> tuple[dict[int, tuple[int, bytes]], int]:
    """One header at `at`: {tag: (type, its data from its offset)} and the offset just past it."""
    if blob[at:at + 4] != _HEADER_MAGIC:
        raise SystemExit(f"no RPM header at {at}")
    count, size = struct.unpack(">II", blob[at + 8:at + 16])
    index = at + 16
    store = index + 16 * count
    tags = {}
    for i in range(count):
        tag, kind, offset, _n = struct.unpack(">IIII", blob[index + 16 * i:index + 16 * i + 16])
        tags[tag] = (kind, blob[store + offset:store + size])
    return tags, store + size


def payload(blob: bytes) -> bytes:
    """The cpio archive an RPM carries, decompressed."""
    if blob[:4] != b"\xed\xab\xee\xdb":
        raise SystemExit("not an RPM (no lead)")
    _signature, end = _header(blob, 96)
    end += -end % 8  # the signature header is padded to eight bytes
    tags, end = _header(blob, end)
    kind, data = tags.get(_PAYLOADCOMPRESSOR, (_STRING, b"gzip\0"))
    compressor = data.split(b"\0", 1)[0].decode()
    if compressor in ("xz", "lzma"):
        return lzma.decompress(blob[end:])
    if compressor == "gzip":
        import gzip

        return gzip.decompress(blob[end:])
    raise SystemExit(f"payload compressor {compressor!r}: not one this reader takes (xz, lzma, gzip)")


def entries(archive: bytes):
    """The cpio "newc" archive's entries: (name, mode, ino, nlink, data), to its trailer."""
    stream = io.BytesIO(archive)
    while True:
        head = stream.read(110)
        if head[:6] not in (b"070701", b"070702"):
            raise SystemExit(f"cpio: no newc header at {stream.tell() - len(head)}")
        fields = [int(head[6 + 8 * i:14 + 8 * i], 16) for i in range(13)]
        ino, mode, nlink, size, namesize = fields[0], fields[1], fields[4], fields[6], fields[11]
        name = stream.read(namesize)[:-1].decode()
        stream.read(-(110 + namesize) % 4)
        data = stream.read(size)
        stream.read(-size % 4)
        if name == "TRAILER!!!":
            return
        yield name, mode, ino, nlink, data


def _inside(root: Path, name: str) -> Path:
    relative = os.path.normpath(name.lstrip("/"))
    if relative == ".." or relative.startswith("../"):
        raise SystemExit(f"{name}: leaves the root")
    return root / relative


def unpack(rpm: Path, root: Path) -> int:
    """Every entry of one RPM under `root`; the number written."""
    written, links = 0, {}
    for name, mode, ino, nlink, data in entries(payload(rpm.read_bytes())):
        if _SKIPPED.search(name):
            continue
        path = _inside(root, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        if stat.S_ISDIR(mode):
            path.mkdir(exist_ok=True)
            continue
        if path.is_symlink() or path.exists():
            path.unlink()
        if stat.S_ISLNK(mode):
            target = data.decode()
            if target.startswith("/"):
                #: a link to an absolute path is a link within the root, as if the root were `/`
                target = os.path.relpath(root / target.lstrip("/"), path.parent)
            path.symlink_to(target)
        elif stat.S_ISREG(mode):
            if nlink > 1:
                #: newc writes a hard link group's data once, with its last name
                links.setdefault(ino, []).append(path)
                if not data:
                    continue
                for other in links[ino]:
                    other.write_bytes(data)
                    other.chmod(0o755 if mode & 0o111 else 0o644)
            else:
                path.write_bytes(data)
                path.chmod(0o755 if mode & 0o111 else 0o644)
        else:
            continue
        written += 1
    return written


#: the top directories a merged /usr holds (EL8's `filesystem` package makes each a link into /usr)
_MERGED = ("bin", "sbin", "lib", "lib64")


def usrmerge(root: Path) -> None:
    """`/bin`, `/sbin`, `/lib` and `/lib64` as links into `/usr`, their files moved there, as the `filesystem` package
    lays the root out: a path an RPM wrote under either name is found under both."""
    for name in _MERGED:
        top, usr = root / name, root / "usr" / name
        if top.is_symlink() or not top.exists():
            continue
        usr.mkdir(parents=True, exist_ok=True)
        for entry in sorted(top.iterdir()):
            target = usr / entry.name
            held = target.exists() or target.is_symlink()
            if held and (entry.is_symlink() or not entry.is_dir()):
                #: both names held it: /usr's copy is the one kept
                entry.unlink()
                continue
            if held:
                #: a directory under both names: one directory
                shutil.copytree(entry, target, symlinks=True, dirs_exist_ok=True)
                shutil.rmtree(entry)
                continue
            if entry.is_symlink() and not os.readlink(entry).startswith("/"):
                #: a relative link keeps its target from its new place
                pointed = os.path.normpath(top / os.readlink(entry))
                entry.unlink()
                target.symlink_to(os.path.relpath(pointed, usr))
                continue
            entry.rename(target)
        top.rmdir()
        top.symlink_to(f"usr/{name}")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="rpm_root.py")
    ap.add_argument("--out", type=Path, required=True, help="the root the RPMs are unpacked into")
    ap.add_argument("rpms", type=Path, nargs="+")
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    for rpm in a.rpms:
        print(f"{rpm.name}: {unpack(rpm, a.out)} entries")
    usrmerge(a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
