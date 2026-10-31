#!/usr/bin/env python3
"""Offline regression: the mock must log even when the log directory is absent.

A fresh checkout has no log directory, and the server's first line on startup is a log write. Without the directory guard the process dies with FileNotFoundError before it can serve a
request, which is what a contributor sees on their first `python3 mock_appserver.py 8096`.
"""
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import mock_appserver as mock  # noqa: E402


class LogDirectoryTest(unittest.TestCase):
    def test_slog_creates_a_missing_log_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "missing", "logs", "server.log")
            old = os.environ.get("GUL_LOG")
            os.environ["GUL_LOG"] = path
            try:
                mock.slog("hello")
            finally:
                if old is None:
                    os.environ.pop("GUL_LOG", None)
                else:
                    os.environ["GUL_LOG"] = old
            self.assertTrue(os.path.isfile(path))
            with open(path) as f:
                self.assertIn("hello", f.read())


if __name__ == "__main__":
    unittest.main()
