#!/usr/bin/env python3
"""Malformed requests must not take the mock down or produce a half-parsed reply.

The mock faces whatever a client (or a curious tool) sends. A decoder that raises inside the handler would surface
as a 500 or a traceback in the service, and the two-client LAN tests would blame the client for it. These tests
pin the answers that exist today: every malformed input gets a complete reply, the remoting envelope still parses,
and the server is still serving afterwards.

No defects were found when these were written; that is why they are worth pinning — the next "stricter parsing"
change is exactly what would break them.
"""

import json
import os
import sys
import unittest
import urllib.error
import urllib.request

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)

import test_mock_branch_b as base  # noqa: E402

REMOTING = "/remoting/appserver/startSession"


class MalformedInputTest(unittest.TestCase):
    def _server(self):
        server = base.MockStartSessionServer({"GUL_USER_WATCH_HOLD": "0", "GUL_STREAM": "1"})
        self.addCleanup(server.close)
        return server

    @staticmethod
    def _call(server, method, path, body=None, ctype="application/x-www-form-urlencoded"):
        request = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (server.port, path), data=body, method=method,
            headers={"Content-Type": ctype} if body is not None else {})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_malformed_remoting_payloads_still_get_a_parsable_envelope(self):
        server = self._server()
        payloads = {
            "garbage": b"__x=GARBAGE",
            "truncated": b"__x=naoy17:track",
            "bad-utf8": b"__x=\xff\xfe\x00",
            "wrong-arity": b"__x=naoy1:iy1:1gh",
            "large": b"__x=" + b"A" * (200 * 1024),
        }
        for label, body in payloads.items():
            with self.subTest(case=label):
                status, data = self._call(server, "POST", REMOTING, body)
                self.assertEqual(status, 200, data[:80])
                self.assertTrue(data.startswith(b"hxr"), data[:40])
                base._parse_reply(data)          # the envelope must still consume fully

    def test_a_body_that_is_not_json_does_not_break_a_json_leg(self):
        server = self._server()
        status, data = self._call(server, "POST", "/parties/create", b"not-json",
                                  ctype="application/json")
        self.assertEqual(status, 200)
        self.assertIn("id", json.loads(data.decode("utf-8")))

    def test_a_malformed_vote_is_rejected_cleanly(self):
        server = self._server()
        status, data = self._call(server, "POST", "/vote", b"{bad", ctype="application/json")
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(data.decode("utf-8"))["accepted"])

    def test_odd_paths_fall_back_rather_than_erroring(self):
        server = self._server()
        for path in ("/users/", "/user/../user", "/channels/%00/poll", "/users/%2e%2e%2fetc/updates/1"):
            with self.subTest(path=path):
                status, data = self._call(server, "GET", path)
                self.assertEqual(status, 200, data[:60])

    def test_an_unsupported_method_is_refused_by_the_http_layer(self):
        # 501 comes from BaseHTTPRequestHandler, not from a crash in the mock.
        server = self._server()
        status, _data = self._call(server, "DELETE", "/user")
        self.assertIn(status, (405, 501))

    def test_the_server_is_still_serving_after_all_of_that(self):
        server = self._server()
        self._call(server, "POST", REMOTING, b"__x=GARBAGE")
        self._call(server, "GET", "/users/")
        status, data = self._call(server, "POST", REMOTING, b"__x=nah")
        self.assertEqual(status, 200)
        self.assertTrue(data.startswith(b"hxr"))


if __name__ == "__main__":
    unittest.main()
