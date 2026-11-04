#!/usr/bin/env python3
"""Exercise `tools/pak_patch_url.py` on a synthetic pak.

The tool rewrites a shipped file in place and refreshes three hashes, so a mistake is not a wrong answer but a
corrupted install. Building a miniature pak with the documented layout lets the whole cycle run offline: patch,
verify the payload and all three hashes, then patch back and confirm the file returns to its original bytes.

No game data is used or needed.
"""

import hashlib
import os
import struct
import subprocess
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)
TOOL = os.path.join(ROOT, "tools", "pak_patch_url.py")

MAGIC = 0x5A6F12E1
ENTRY_TAIL = 53
HASH_AT = 28


def fstring(text):
    raw = text.encode("latin-1")
    return struct.pack("<i", len(raw)) + raw


def build_pak(path, payload, entry_path="hns/Content/Dummy.uasset", mount="../../../"):
    """Write a one-entry v3 pak with uncompressed data, hashes consistent."""
    entry_offset = 0
    digest = hashlib.sha1(payload).digest()
    header = (struct.pack("<QQQHH", entry_offset, len(payload), len(payload), 0, 0)
              + digest + b"\x00" * 5)
    assert len(header) == ENTRY_TAIL

    body = header + payload
    prefix = fstring(mount) + struct.pack("<I", 1) + fstring(entry_path)
    index = prefix + header
    index_offset = len(body)
    footer = struct.pack("<IIQQ", MAGIC, 3, index_offset, len(index)) + hashlib.sha1(index).digest()
    with open(path, "wb") as handle:
        handle.write(body + index + footer)
    return {"payload_at": ENTRY_TAIL, "index_at": index_offset, "index_len": len(index),
            "tail_at": len(prefix), "size": len(body + index + footer)}


class PakPatchUrlTest(unittest.TestCase):
    def _pak(self, payload):
        handle, path = tempfile.mkstemp(suffix=".pak")
        os.close(handle)
        self.addCleanup(os.unlink, path)
        return path, build_pak(path, payload)

    def _run(self, *args):
        return subprocess.run([sys.executable, TOOL, *args], capture_output=True, text=True, timeout=60)

    def _hashes(self, path, layout, payload):
        with open(path, "rb") as handle:
            data = handle.read()
        digest = hashlib.sha1(payload).digest()
        index = data[layout["index_at"]:layout["index_at"] + layout["index_len"]]
        footer_hash = data[-20:]
        hash_at = layout["tail_at"] + HASH_AT
        return {
            "payload": data[layout["payload_at"]:layout["payload_at"] + len(payload)],
            "disk_hash": data[HASH_AT:HASH_AT + 20],
            "index_hash": index[hash_at:hash_at + 20],
            "footer_hash": footer_hash,
            "digest": digest,
            "index_sha": hashlib.sha1(index).digest(),
        }

    def test_a_same_length_patch_rewrites_the_url_and_all_three_hashes(self):
        old = b"http://api.playstreamline.com"
        new = b"http://127.0.0.1:8096////////"
        self.assertEqual(len(old), len(new))
        path, layout = self._pak(b"...." + old + b"....")
        result = self._run(path, old.decode(), new.decode())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        hashes = self._hashes(path, layout, b"...." + new + b"....")
        self.assertEqual(hashes["payload"], b"...." + new + b"....")
        self.assertEqual(hashes["disk_hash"], hashes["digest"], "on-disk header hash not refreshed")
        self.assertEqual(hashes["index_hash"], hashes["digest"], "index hash not refreshed")
        self.assertEqual(hashes["footer_hash"], hashes["index_sha"], "footer index hash not refreshed")

    def test_patching_back_restores_the_original_bytes(self):
        old = b"http://api.playstreamline.com"
        new = b"http://127.0.0.1:8096////////"
        path, _layout = self._pak(b"...." + old + b"....")
        with open(path, "rb") as handle:
            original = handle.read()
        self.assertEqual(self._run(path, old.decode(), new.decode()).returncode, 0)
        self.assertEqual(self._run(path, new.decode(), old.decode()).returncode, 0)
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), original, "the round trip did not restore the file")

    def test_a_length_mismatch_is_refused_before_touching_the_file(self):
        path, _layout = self._pak(b"http://api.playstreamline.com")
        with open(path, "rb") as handle:
            before = handle.read()
        result = self._run(path, "http://api.playstreamline.com", "http://127.0.0.1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("LENGTH MISMATCH", result.stdout)
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), before, "a refused patch modified the file")

    def test_self_heal_mode_fixes_a_corrupted_hash_without_a_url(self):
        payload = b"payload-without-a-url"
        path, layout = self._pak(payload)
        # Corrupt the on-disk header hash, as a truncated earlier patch would.
        with open(path, "r+b") as handle:
            handle.seek(HASH_AT)
            handle.write(b"\x00" * 20)
        result = self._run(path)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        hashes = self._hashes(path, layout, payload)
        self.assertEqual(hashes["disk_hash"], hashes["digest"])
        self.assertEqual(hashes["footer_hash"], hashes["index_sha"])

    def test_a_file_that_is_not_a_pak_is_refused(self):
        handle, path = tempfile.mkstemp(suffix=".pak")
        os.write(handle, b"not a pak at all")
        os.close(handle)
        self.addCleanup(os.unlink, path)
        result = self._run(path)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), b"not a pak at all", "a refused file was modified")


if __name__ == "__main__":
    unittest.main()
