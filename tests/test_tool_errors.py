#!/usr/bin/env python3
"""Offline regression: the published tools fail cleanly on a bad path.

A contributor's first encounter with a tool is often a wrong path. The tools must print a short
message and a non-zero status instead of a Python traceback.
"""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MISSING = os.path.join(tempfile.gettempdir(), "definitely-not-here", "input.bin")


def run(*args):
    return subprocess.run([sys.executable] + list(args), cwd=ROOT,
                          capture_output=True, text=True, timeout=60)


class ToolErrorTest(unittest.TestCase):
    def assert_clean(self, proc):
        text = proc.stdout + proc.stderr
        self.assertNotIn("Traceback", text)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("error", text.lower())

    def test_patch_client_missing_input(self):
        self.assert_clean(run(os.path.join("tools", "patch_client.py"),
                              "--profile", "host", "--in", MISSING,
                              "--out", os.path.join(tempfile.gettempdir(), "x.exe")))

    def test_fix_checksum_missing_input(self):
        self.assert_clean(run(os.path.join("tools", "fix_pe_checksum.py"), MISSING))

    def test_pak_patch_missing_input(self):
        self.assert_clean(run(os.path.join("tools", "pak_patch_url.py"), MISSING,
                              "https://api.playstreamline.com",
                              "http://127.0.0.1:8096/////////"))


if __name__ == "__main__":
    unittest.main()
