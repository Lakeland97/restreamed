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
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)

import reply_walk  # noqa: E402
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

    def _handshake(self, extra_env=None):
        server = self._server(extra_env)
        return reply_walk.success_returnval(server.call_op("doHandshake"))

    def test_the_base_url_is_used_for_the_service_fields(self):
        value = self._handshake({"GUL_BASE": "http://10.0.0.5:8096"})
        self.assertEqual(value["pgsServer"], "http://10.0.0.5:8096/")
        self.assertEqual(value["pgsFrontend"], "http://10.0.0.5:8096/")
        self.assertEqual(value["api"], "http://10.0.0.5:8096")

    def test_server_environment_uses_the_knob(self):
        self.assertEqual(self._handshake()["serverEnvironment"], "local")
        self.assertEqual(self._handshake({"GUL_SERVER_ENV": "staging"})["serverEnvironment"], "staging")

    def test_cooldown_uses_the_knob(self):
        self.assertEqual(self._handshake()["hostedAutojoinCooldownSecs"], 10)
        self.assertEqual(self._handshake({"GUL_COOLDOWN": "42"})["hostedAutojoinCooldownSecs"], 42)

    def test_fake_player_is_written_to_the_config(self):
        self.assertEqual(self._handshake({"GUL_FAKE_PLAYER": "bot-7"})["fakePlayerID"], "bot-7")

    def _party_ids_after_two_logins(self, window):
        """Two identities, then read the party as the second; return the ids it lists."""
        from test_session_identity import _login_body, _post
        server = self._server({"GUL_COOKIES": "1", "GUL_PGS_PARTY_WINDOW": window,
                               "GUL_IID": "", "GUL_NAME": ""})   # clear the harness's single-client pins
        _post(server, "/remoting/appserver/startSession", _login_body("W" * 32))
        time.sleep(0.05)                      # the first identity is now older than a 0-second window
        _body, headers = _post(server, "/remoting/appserver/startSession", _login_body("X" * 32))
        cookie = (headers.get("Set-Cookie") or "").split(";", 1)[0]
        request = urllib.request.Request("http://127.0.0.1:%d/parties" % server.port,
                                         headers={"Cookie": cookie})
        with urllib.request.urlopen(request, timeout=20) as response:
            party = json.loads(response.read().decode("utf-8"))
        return {user["playerIID"] for user in party["users"]}

    def test_the_party_window_drops_members_that_are_too_old(self):
        self.assertIn("player1", self._party_ids_after_two_logins("300"))
        self.assertNotIn("player1", self._party_ids_after_two_logins("0"))

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
