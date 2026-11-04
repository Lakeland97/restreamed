#!/usr/bin/env python3
"""Round-trip `tools/pakdump.py` against a pak this test writes itself.

`pak_patch_url.py` writes the same layout `pakdump.py` reads, and both are the first tools anyone runs on a pak.
The synthetic builder from the patcher test is reused here so the reader is verified against a file whose bytes
are known exactly — no shipped pak needed — and then the shipped pak is used for a much weaker, structural check.

The point is the direction that normally goes untested: a reader that silently accepts a file it does not
understand (empty listing, wrong sizes) would look fine in a smoke test.
"""

import os
import subprocess
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, TESTS_DIR)

try:
    import pakdump  # noqa: E402
except Exception:  # pragma: no cover
    pakdump = None

SHIPPED_PAK = os.path.join(ROOT, "game", "hns", "Content", "Paks", "hns-WindowsNoEditor.pak")

try:
    from test_pak_patch_url import build_pak  # noqa: E402
except Exception:  # pragma: no cover
    build_pak = None


@unittest.skipUnless(pakdump is not None and build_pak is not None, "pak tools unavailable")
class PakdumpRoundTripTest(unittest.TestCase):
    def _pak(self, payload, entry_path="hns/Content/Dummy.uasset"):
        handle, path = tempfile.mkstemp(suffix=".pak")
        os.close(handle)
        self.addCleanup(os.unlink, path)
        layout = build_pak(path, payload, entry_path=entry_path)
        return path, layout

    def test_the_index_reads_back_with_the_entry_we_wrote(self):
        payload = b"payload-bytes-for-the-entry" * 3
        path, _layout = self._pak(payload)
        with pakdump.Pak(path) as pak:
            entries = pak.entries
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["path"], "hns/Content/Dummy.uasset")
        self.assertEqual(entry["size"], len(payload))

    def test_extraction_returns_the_exact_payload(self):
        payload = bytes(range(256)) * 4
        path, _layout = self._pak(payload)
        with pakdump.Pak(path) as pak:
            data = pak.read_entry(pak.entries[0])
        self.assertEqual(data, payload)

    def test_the_list_command_names_the_entry(self):
        path, _layout = self._pak(b"abc")
        result = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "pakdump.py"),
                                 path, "list"], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Dummy.uasset", result.stdout)

    def test_a_file_that_is_not_a_pak_is_rejected(self):
        handle, path = tempfile.mkstemp(suffix=".pak")
        os.write(handle, b"not a pak")
        os.close(handle)
        self.addCleanup(os.unlink, path)
        with self.assertRaises(Exception):
            pakdump.Pak(path)

    def test_a_truncated_index_is_rejected_rather_than_half_read(self):
        path, layout = self._pak(b"abcdef")
        with open(path, "r+b") as handle:
            handle.truncate(layout["size"] - 10)
        with self.assertRaises(Exception):
            pakdump.Pak(path)


@unittest.skipUnless(pakdump is not None and os.path.exists(SHIPPED_PAK),
                     "shipped pak is absent (game/ is not in the repo)")
class ShippedPakTest(unittest.TestCase):
    def test_the_shipped_pak_has_ten_thousand_entries_and_named_paths(self):
        with pakdump.Pak(SHIPPED_PAK) as pak:
            self.assertGreater(len(pak.entries), 10000)
            paths = [entry["path"] for entry in pak.entries[:200]]
        self.assertTrue(any(path.startswith("hns/Content/") for path in paths), paths[:5])


if __name__ == "__main__":
    unittest.main()
