#!/usr/bin/env python3
"""HTTP behaviour of the stream routes, not just the engine behind them.

`test_stream_engine.py` pins selection; these pin what a caller sees. The route that matters here is
`/stream/profile`: its mode comes from `GUL_STREAM_MODE`, and only `tag` has a decoded allow-list. A value naming
another mode used to raise inside the handler — the connection dropped and the service log got a traceback — so a
caller had no way to learn which knob was wrong. It must answer with the reason instead, and the routes that do not
need the allow-list must keep working.
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


def get(server, path):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (server.port, path), timeout=20) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class StreamRouteTest(unittest.TestCase):
    def _server(self, extra_env=None):
        server = base.MockStartSessionServer(extra_env or {})
        self.addCleanup(server.close)
        return server

    def test_the_profile_answers_for_the_default_mode(self):
        server = self._server()
        status, body = get(server, "/stream/profile")
        self.assertEqual(status, 200)
        profile = json.loads(body.decode("utf-8"))
        self.assertEqual(profile["mode"], "tag")
        self.assertEqual(profile["pool_size"], 22)
        self.assertIn("rotation_seed", profile)

    def test_an_undecoded_mode_answers_with_the_reason(self):
        server = self._server({"GUL_STREAM_MODE": "bitball"})
        status, body = get(server, "/stream/profile")
        self.assertEqual(status, 400, body[:120])
        error = json.loads(body.decode("utf-8"))["error"]
        self.assertIn("bitball", error)
        self.assertIn("decoded modes: tag", error)

        # The rotation does not consult the allow-list, so it must keep answering.
        for path in ("/channels/x/mods", "/channels/x/mods/current"):
            status, _body = get(server, path)
            self.assertEqual(status, 200, path)


if __name__ == "__main__":
    unittest.main()
