#!/usr/bin/env python3
"""Tests for `tools/verify_server.py`, the conformance checker for other server implementations.

The tool's job is to fail a bad server, so both directions are pinned: it passes the reference mock, and it
fails a server that answers plausible-looking nonsense. Without the negative case a checker that always
prints PASS would look healthy.
"""

import http.server
import json
import os
import subprocess
import sys
import threading
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)
sys.path.insert(0, TESTS_DIR)

import test_mock_branch_b as base  # noqa: E402

TOOL = os.path.join(ROOT, "tools", "verify_server.py")


def _run(base_url):
    return subprocess.run([sys.executable, TOOL, "--base", base_url],
                          capture_output=True, text=True, timeout=120)


class _NonsenseHandler(http.server.BaseHTTPRequestHandler):
    """Answers every request 200 with JSON that carries none of the required shapes."""

    def do_GET(self):
        self._answer()

    def do_POST(self):
        self._answer()

    def _answer(self):
        body = json.dumps({"unexpected": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class VerifyServerTest(unittest.TestCase):
    def test_the_reference_mock_passes_every_leg(self):
        server = base.MockStartSessionServer({"GUL_USER_WATCH_HOLD": "0"})
        self.addCleanup(server.close)
        result = _run("http://127.0.0.1:%d" % server.port)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("6/6 legs pass", result.stdout)

    def test_a_server_with_no_required_shapes_fails(self):
        stub = http.server.HTTPServer(("127.0.0.1", 0), _NonsenseHandler)
        thread = threading.Thread(target=stub.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(stub.server_close)
        self.addCleanup(stub.shutdown)
        result = _run("http://127.0.0.1:%d" % stub.server_port)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("FAIL", result.stdout)

    def test_an_unreachable_base_exits_two(self):
        result = _run("http://127.0.0.1:1")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
