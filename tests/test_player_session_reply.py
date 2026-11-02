#!/usr/bin/env python3
"""`loadPlayerFromSession` must hand the client its token, or the channel watcher never starts.

The client's login path is:

    POST .../appserver-pvt/loadPlayerFromSession
      -> reply.returnVal.pgsToken copied into LoggedInPlayer+0x20 (+0x140D4766D)
      -> verifyWithPGS: empty token returns before its `GET user` (0x140D4889B)
      -> that GET's continuation is what starts the user watcher and, when users[0].roles contains
         `broadcaster`, the channel watcher.

The mock answered this op with the handshake config, which has no `pgsToken` at all, so the chain
stopped at step one and no run ever issued a `user` GET. These tests pin the player record on this
op, and pin that the config legs are unchanged.

No client and no game: the mock runs on an ephemeral port.
"""

import json
import os
import sys
import unittest
import urllib.parse
import urllib.request

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)

import test_mock_branch_b as base  # noqa: E402


class PlayerSessionReplyTest(unittest.TestCase):
    def _server(self, extra_env=None):
        server = base.MockStartSessionServer(extra_env or {})
        self.addCleanup(server.close)
        return server

    def _op(self, server, path, body=b"__x=nah"):
        request = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (server.port, path),
            data=body,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return base._parse_reply(response.read()).return_val

    def test_the_session_reply_carries_a_non_empty_pgs_token(self):
        server = self._server()
        record = self._op(server, "/remoting/appserver-pvt/loadPlayerFromSession")
        self.assertIsInstance(record, dict, record)
        token = record.get("pgsToken")
        self.assertTrue(isinstance(token, str) and token, record)

    def test_the_session_reply_carries_the_session_and_player_identifiers(self):
        server = self._server()
        record = self._op(server, "/remoting/appserver-pvt/loadPlayerFromSession")
        for field in ("playerSessionID", "playerIID", "accountID", "displayName"):
            with self.subTest(field=field):
                self.assertTrue(record.get(field), record)

    def test_the_handshake_config_still_carries_no_token(self):
        # The config leg is not the player record; adding the token there would change bytes the
        # client parses as config for every handshake.
        server = self._server()
        record = self._op(server, "/remoting/appserver/doHandshake")
        self.assertNotIn("pgsToken", record, "the handshake config must stay token-free")
        self.assertIn("pgsServer", record)

    def test_the_token_matches_the_one_the_auth_leg_serves(self):
        server = self._server()
        record = self._op(server, "/remoting/appserver-pvt/loadPlayerFromSession")
        request = urllib.request.Request(
            "http://127.0.0.1:%d/auth/check" % server.port)
        with urllib.request.urlopen(request, timeout=10) as response:
            auth = json.loads(response.read().decode("utf-8"))
        self.assertEqual(record["pgsToken"], auth["body"],
                         "both legs describe one session, so they must agree on the token")

    def test_the_config_merge_knob_still_applies(self):
        # GUL_CTX_INTO_CONFIG keeps merging identity into the handshake context; this op must not
        # depend on that knob for the token.
        server = self._server({"GUL_CTX_INTO_CONFIG": "0"})
        record = self._op(server, "/remoting/appserver-pvt/loadPlayerFromSession")
        self.assertTrue(record.get("pgsToken"), record)


if __name__ == "__main__":
    unittest.main()
