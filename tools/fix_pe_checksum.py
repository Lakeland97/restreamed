#!/usr/bin/env python3
"""Recompute the PE optional-header checksum after patching Streamline.exe.

The client's FakeID DRM path rejects a modified executable unless the PE
checksum matches the file contents (observed: patched bytes with the stale
checksum -> "Streamline must be run online through Steam or Twitch Launcher";
same bytes with a recomputed checksum -> "Using DRM: FakeID(restrea1)").

Usage: python3 tools/fix_pe_checksum.py path/to/Streamline.exe
"""
import struct
import sys


def fix(path: str) -> int:
    data = bytearray(open(path, "rb").read())
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise SystemExit("not a PE file")
    csum_off = pe + 0x58
    old = struct.unpack_from("<I", data, csum_off)[0]
    struct.pack_into("<I", data, csum_off, 0)
    total = 0
    for i in range(0, len(data), 2):
        if i == csum_off:
            continue
        word = data[i] | ((data[i + 1] if i + 1 < len(data) else 0) << 8)
        total += word
        total = (total & 0xFFFF) + (total >> 16)
    total = (total & 0xFFFF) + (total >> 16)
    total = (total + len(data)) & 0xFFFFFFFF
    struct.pack_into("<I", data, csum_off, total)
    open(path, "wb").write(data)
    print("checksum %#x -> %#x  %s" % (old, total, path))
    return 0


def check(path: str) -> int:
    """Report stored vs computed without writing (the shipped exe is NOT self-consistent)."""
    data = bytearray(open(path, "rb").read())
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise SystemExit("not a PE file")
    csum_off = pe + 0x58
    stored = struct.unpack_from("<I", data, csum_off)[0]
    struct.pack_into("<I", data, csum_off, 0)
    total = 0
    for i in range(0, len(data), 2):
        if i == csum_off:
            continue
        word = data[i] | ((data[i + 1] if i + 1 < len(data) else 0) << 8)
        total += word
        total = (total & 0xFFFF) + (total >> 16)
    total = (total & 0xFFFF) + (total >> 16)
    computed = (total + len(data)) & 0xFFFFFFFF
    print("stored    %#010x" % stored)
    print("computed  %#010x" % computed)
    print("delta     %#x" % (computed - stored if computed >= stored else stored - computed))
    return 0 if stored == computed else 1


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--check":
        sys.exit(check(sys.argv[2]))
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    sys.exit(fix(sys.argv[1]))
