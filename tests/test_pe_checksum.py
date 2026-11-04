#!/usr/bin/env python3
"""Cross-check `tools/fix_pe_checksum.py` against the PE specification.

The tool recomputes the optional-header checksum that the client's DRM path wants after a patch, and
`tools/patch_client.py` imports the same two functions so the patcher cannot disagree with it. That makes a wrong
implementation expensive: a stale checksum rejects the client on a technicality, and a too-clever one that
"works" for this file may not be the specified algorithm at all.

So the test computes the value the PE specification describes with a plain per-byte loop and requires the tool's
array-based implementation to agree, on a synthetic image and on random buffers. No game file is needed; the
shipped exe is used only for the stability checks at the end.
"""

import os
import random
import struct
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)
sys.path.insert(0, os.path.join(ROOT, "tools"))

try:
    import fix_pe_checksum as pec  # noqa: E402
except Exception:  # pragma: no cover
    pec = None

SHIPPED = os.path.join(ROOT, "game", "hns", "Binaries", "Win64", "Streamline.exe")


def build_pe(payload_size=256, seed=1):
    """A minimal but structurally valid PE: DOS header, PE signature, optional header, payload."""
    rnd = random.Random(seed)
    payload = bytes(rnd.randrange(256) for _ in range(payload_size))
    dos = bytearray(0x40)
    struct.pack_into("<I", dos, 0x3C, 0x40)          # e_lfanew -> PE signature at 0x40
    pe = bytearray(b"PE\0\0")
    optional = bytearray(0xE0)                        # enough for the CheckSum field at +0x58
    return dos + pe + optional + bytearray(payload)


def reference_checksum(data):
    """The PE spec's algorithm, written the slow obvious way."""
    csum_off = struct.unpack_from("<I", data, 0x3C)[0] + 0x58
    total = 0
    length = len(data)
    for index in range(0, length - (length & 1), 2):
        if index in (csum_off, csum_off + 2):
            continue                                  # the field counts as zero
        total += data[index] | (data[index + 1] << 8)
        total = (total & 0xFFFF) + (total >> 16)      # fold as we go
    if length & 1:
        total += data[-1]
        total = (total & 0xFFFF) + (total >> 16)
    total = (total & 0xFFFF) + (total >> 16)
    return (total + length) & 0xFFFFFFFF


@unittest.skipUnless(pec is not None, "checksum tool unavailable")
class PeChecksumTest(unittest.TestCase):
    def test_the_checksum_offset_is_where_the_spec_says(self):
        data = build_pe()
        expected = struct.unpack_from("<I", data, 0x3C)[0] + 0x58
        self.assertEqual(pec.checksum_offset(data), expected)

    def test_a_non_pe_is_rejected(self):
        with self.assertRaises(SystemExit):
            pec.checksum_offset(bytearray(b"definitely not a PE" + b"\x00" * 0x40))

    def test_the_value_matches_the_specification_algorithm(self):
        for size, seed in ((64, 1), (255, 2), (256, 3), (4096, 4)):
            with self.subTest(size=size, seed=seed):
                data = build_pe(payload_size=size, seed=seed)
                self.assertEqual(pec.compute_checksum(data), reference_checksum(data))

    def test_an_odd_length_file_matches_too(self):
        data = build_pe() + b"\x7f"                       # odd total length
        self.assertEqual(pec.compute_checksum(data), reference_checksum(data))

    def test_the_stored_value_does_not_influence_the_result(self):
        # The field is excluded from the sum, so writing garbage into it must change nothing.
        data = build_pe()
        before = pec.compute_checksum(data)
        struct.pack_into("<I", data, pec.checksum_offset(data), 0xDEADBEEF)
        self.assertEqual(pec.compute_checksum(data), before)

    def test_a_real_change_does_change_the_result(self):
        data = build_pe()
        before = pec.compute_checksum(data)
        data[-1] ^= 0xFF
        self.assertNotEqual(pec.compute_checksum(data), before)


@unittest.skipUnless(pec is not None and os.path.exists(SHIPPED),
                     "shipped exe is absent (game/ is not in the repo)")
class ShippedImageTest(unittest.TestCase):
    def test_the_shipped_image_matches_the_specification_algorithm(self):
        with open(SHIPPED, "rb") as handle:
            data = bytearray(handle.read())
        self.assertEqual(pec.compute_checksum(data), reference_checksum(data))

    def test_the_shipped_image_is_not_self_consistent_and_check_says_so(self):
        # Documented on the wiki: retail stores a value below the content-derived one, which is why patching
        # without recomputing fails the DRM path.
        import subprocess
        result = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "fix_pe_checksum.py"),
                                 "--check", SHIPPED], capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 1, result.stdout)   # differs -> exit 1
        self.assertIn("stored", result.stdout)
        self.assertIn("computed", result.stdout)


if __name__ == "__main__":
    unittest.main()
