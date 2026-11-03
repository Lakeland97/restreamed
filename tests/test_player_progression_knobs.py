#!/usr/bin/env python3
"""The progression fields a server reports, and what they are not.

The client's home-UI builder sets its persisted `PlayedFirstQuickMatch` when the flag is already true or
when the **local player's** `AdvancementComponent` reads a level above 1 or XP above 0
(`0x14067E0F4`-`0x14067E156` via `0x140BD2BA0`/`0x140BD2E10`). The session record's `lastPlayerLevel` and
`lastXpIntoLevel` are written *from* that component (`cachePlayerLevel`, `0x1407D5300`), so serving them is
reporting state, **not** a way to grant progress — a server cannot raise them into the flag.

These tests pin that reporting: the three fields exist on the served player record, default to a fresh
profile, and follow the knobs.

No client and no game: the mock runs on an ephemeral port.
"""

import json
import os
import sys
import unittest
import urllib.request

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)

import test_mock_branch_b as base  # noqa: E402


class ProgressionKnobsTest(unittest.TestCase):
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

    def test_the_session_record_carries_a_fresh_profiles_progression(self):
        server = self._server()
        record = self._op(server, "/remoting/appserver-pvt/loadPlayerFromSession")
        self.assertEqual(record.get("lastPlayerLevel"), 1, record)
        self.assertEqual(record.get("lastXpIntoLevel"), 0, record)
        self.assertGreater(record.get("lastXpForNextLevel"), 0, record)

    def test_the_level_knob_reaches_the_reply(self):
        server = self._server({"GUL_PLAYER_LEVEL": "4"})
        record = self._op(server, "/remoting/appserver-pvt/loadPlayerFromSession")
        self.assertEqual(record.get("lastPlayerLevel"), 4, record)

    def test_the_xp_knob_reaches_the_reply(self):
        server = self._server({"GUL_PLAYER_XP": "250", "GUL_PLAYER_XP_NEXT": "500"})
        record = self._op(server, "/remoting/appserver-pvt/loadPlayerFromSession")
        self.assertEqual(record.get("lastXpIntoLevel"), 250, record)
        self.assertEqual(record.get("lastXpForNextLevel"), 500, record)

    def test_the_knobs_do_not_leak_into_unrelated_fields(self):
        server = self._server({"GUL_PLAYER_LEVEL": "9", "GUL_PLAYER_XP": "9"})
        record = self._op(server, "/remoting/appserver-pvt/loadPlayerFromSession")
        self.assertEqual(record.get("playerIID"), "devuser", record)
        self.assertNotIn("level", record, "the field is lastPlayerLevel, not level")

    def test_progression_travels_on_the_player_record_not_the_config_subset(self):
        # /remoting-config serves a deliberate subset (identity, accounts) and has never carried
        # progression; the player record is where the client reads `lastPlayerLevel`. Pinning the split
        # keeps a future "just add it everywhere" change from silently altering a leg's shape.
        server = self._server({"GUL_PLAYER_LEVEL": "3", "GUL_RCONF": "1"})
        request = urllib.request.Request("http://127.0.0.1:%d/remoting-config" % server.port)
        with urllib.request.urlopen(request, timeout=10) as response:
            body = json.loads(response.read().decode("utf-8"))
        self.assertEqual(body.get("playerIID"), "devuser", body)
        self.assertIn("accounts", body, body)
        self.assertNotIn("lastPlayerLevel", body, body)


if __name__ == "__main__":
    unittest.main()
