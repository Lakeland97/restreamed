#!/usr/bin/env python3
"""Recompute the PE optional-header checksum after patching Streamline.exe.

The client's FakeID DRM path rejects a modified executable unless the PE
checksum matches the file contents (observed: patched bytes with the stale
checksum -> "Streamline must be run online through Steam or Twitch Launcher";
same bytes with a recomputed checksum -> "Using DRM: FakeID(restrea1)").

tools/patch_client.py imports `checksum_offset` and `compute_checksum` from
here, so the patcher and this tool can never disagree about the value.

Usage: python3 tools/fix_pe_checksum.py path/to/Streamline.exe
"""
import array
import struct
import sys


def checksum_offset(data) -> int:
    """File offset of the optional-header CheckSum field."""
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise SystemExit("not a PE file")
    return pe + 0x58


def compute_checksum(data) -> int:
    """The checksum this file should carry; the CheckSum field itself counts as zero.

    The 16-bit word sum is taken over an array view (C-speed), then folded twice.
    The field's two words are subtracted instead of being zeroed in a copy, so the
    caller's buffer is never modified.
    """
    csum_off = checksum_offset(data)
    if csum_off % 2:
        raise ValueError("PE checksum field is not 16-bit aligned")
    size = len(data)
    words = array.array("H")
    words.frombytes(memoryview(data)[:size - (size & 1)])
    if sys.byteorder != "little":
        words.byteswap()
    total = sum(words)
    if size & 1:
        total += data[-1]
    first = csum_off // 2
    for index in (first, first + 1):
        if index < len(words):
            total -= words[index]
    while total > 0xFFFF:
        total = (total & 0xFFFF) + (total >> 16)
    return (total + size) & 0xFFFFFFFF


def fix(path: str) -> int:
    data = bytearray(open(path, "rb").read())
    csum_off = checksum_offset(data)
    old = struct.unpack_from("<I", data, csum_off)[0]
    total = compute_checksum(data)
    struct.pack_into("<I", data, csum_off, total)
    open(path, "wb").write(data)
    print("checksum %#x -> %#x  %s" % (old, total, path))
    return 0


def check(path: str) -> int:
    """Report stored vs computed without writing (the shipped exe is NOT self-consistent)."""
    data = bytearray(open(path, "rb").read())
    stored = struct.unpack_from("<I", data, checksum_offset(data))[0]
    computed = compute_checksum(data)
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
