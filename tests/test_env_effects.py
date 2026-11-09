#!/usr/bin/env python3
"""Every user-facing knob must change the reply it documents.

The README's table says what each knob does; a knob that is read but ignored reads the same in the code and the
table, and only the reply shows the difference. These tests pin one observable per knob, with the default as the
control wherever the value is not otherwise obvious.

Only knobs whose effect is visible without a client are here (JSON legs and one remoting op). The reply-shape
experiment knobs (`GUL_HS_MODE`, the `GUL_DSUR_*` family) are covered by the envelope tests instead.
"""

import json
import os
import sys
import unittest
import urllib.error
import urllib.parse
import urllib.request

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)

import test_mock_branch_b as base  # noqa: E402


def get_json(server, path):
    with urllib.request.urlopen("http://127.0.0.1:%d%s" % (server.port, path), timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def post_reply(server, path, body):
    request = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (server.port, path), data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class EnvEffectTest(unittest.TestCase):
    def _server(self, extra_env=None):
        server = base.MockStartSessionServer(extra_env or {})
        self.addCleanup(server.close)
        return server

    def test_heartbeat_interval_comes_from_the_knob(self):
        self.assertEqual(get_json(self._server(), "/games/streamline/active_user")["interval_sec"], 30)
        self.assertEqual(get_json(self._server({"GUL_HEARTBEAT_SEC": "7"}),
                                  "/games/streamline/active_user")["interval_sec"], 7)

    def test_auth_check_omits_accounts_only_when_asked(self):
        default = get_json(self._server(), "/auth/check")
        self.assertTrue(default["accounts"])
        bare = get_json(self._server({"GUL_AUTH_ACCOUNTS": "0"}), "/auth/check")
        self.assertNotIn("accounts", bare)
        self.assertTrue(bare["body"])

    def test_streamote_flag_marks_the_account(self):
        account = get_json(self._server({"GUL_ACCOUNT_STREAMOTE": "1"}), "/auth/check")["accounts"][0]
        self.assertIs(account["isStreamoteMember"], True)
        self.assertNotIn("isStreamoteMember", get_json(self._server(), "/auth/check")["accounts"][0])

    def test_party_owner_shape_follows_the_knob(self):
        self.assertIsInstance(get_json(self._server(), "/parties")["owner"], str)
        owner = get_json(self._server({"GUL_PGS_PARTY_OWNER_RECORD": "1"}), "/parties")["owner"]
        self.assertEqual(owner["playerIID"], owner["id"])

    def test_current_ip_comes_from_the_knob(self):
        server = self._server({"GUL_PUBLIC_IP": "10.9.8.7"})
        status, reply = post_reply(server, "/remoting/appserver/getCurrentIP", b"__x=n")
        self.assertEqual(status, 200)
        self.assertIn(b"10.9.8.7", reply)
        self.assertNotIn(b"127.0.0.1", reply)

    def test_the_profile_reports_the_window_and_audience(self):
        profile = get_json(self._server({"GUL_STREAM_WINDOW": "9", "GUL_STREAM_AUDIENCE": "5"}),
                           "/stream/profile")
        self.assertEqual((profile["window"], profile["audience"]), (9, 5))

    def test_the_channel_list_uses_the_pinned_channel(self):
        channels = get_json(self._server({"GUL_STREAM": "1", "GUL_STREAM_CHANNEL": "chan42"}), "/channels")
        self.assertEqual(channels["live_channels"][0]["id"], "chan42")

    def test_the_profile_reports_the_seeds(self):
        profile = get_json(self._server({"GUL_STREAM_SEED": "11", "GUL_ROTATION_SEED": "22"}),
                           "/stream/profile")
        self.assertEqual((profile["seed"], profile["rotation_seed"]), (11, 22))

    def test_the_live_channel_reports_the_viewer_count(self):
        channels = get_json(self._server({"GUL_STREAM": "1", "GUL_STREAM_VIEWERS": "77"}), "/channels")
        self.assertEqual(channels["live_channels"][0]["viewers"], 77)

    def test_the_issued_token_is_the_pinned_jwt(self):
        token = "x.eyJ1c2VyIjoicGlubmVkIn0.z"
        self.assertEqual(get_json(self._server({"GUL_JWT": token}), "/auth/check")["body"], token)


if __name__ == "__main__":
    unittest.main()
