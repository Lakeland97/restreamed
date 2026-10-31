#!/usr/bin/env python3
"""Offline tests for the mock's per-session player identity.

One mock serves several clients on a LAN test. Before this change every login was the same
`devuser`/`DevUser`, so two clients shared one identity and collided in the host's contestant
map. The mock now allocates an identity per login, keyed by the client's `trackingSessionID`
(or `streamline_session_id`), the `hns_session` cookie it issues, or the player named in a
`/users/<player>/...` path — never by source IP.

These tests pin:

  * two simulated logins get different `playerIID`/`displayName` (and the rest of the
    identity record: `accountID`, `pgsToken`, accounts);
  * each identity is stable across that session's later ops (repeat `startSession`,
    `postTrackingInfo`, `/remoting-config`, `/parties`, the user watcher);
  * `GUL_IID`/`GUL_NAME` still pin one identity for single-client tests;
  * `GUL_NAME_PREFIX` names the allocated identities.

No client and no game: the mock runs on an ephemeral port like the other offline tests.
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

# Defeat the harness's legacy single-client pins so these tests exercise allocation.
AUTO_IDENTITY = {"GUL_IID": "", "GUL_NAME": ""}


def _post(server, path, body, headers=None):
    request = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (server.port, path),
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", **(headers or {})},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read(), response.headers


def _get(server, path, headers=None):
    request = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (server.port, path),
        headers=headers or {},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read(), response.headers


def _login_body(token):
    return urllib.parse.urlencode({
        "__x": "naoy17:trackingSessionIDy32:%sgh" % token,
    }).encode()


def _tracking_body(token):
    return urllib.parse.urlencode({
        "__x": "naoy12:login_statusy8:LoggedIny21:streamline_session_idy32:%sgh" % token,
    }).encode()


class SessionIdentityTest(unittest.TestCase):
    def _server(self, extra_env=None):
        env = dict(AUTO_IDENTITY)
        env.update(extra_env or {})
        server = base.MockStartSessionServer(env)
        self.addCleanup(server.close)
        return server

    def _login(self, server, token):
        body, headers = _post(server, "/remoting/appserver/startSession", _login_body(token))
        return base._parse_reply(body).return_val, headers

    @staticmethod
    def _cookie(headers):
        value = headers.get("Set-Cookie") or ""
        return value.split(";", 1)[0]

    def test_two_sessions_get_distinct_identities(self):
        server = self._server()
        first, _ = self._login(server, "A" * 32)
        second, _ = self._login(server, "B" * 32)

        self.assertEqual(first["displayName"], "Player1")
        self.assertEqual(second["displayName"], "Player2")
        self.assertEqual(first["playerIID"], "player1")
        self.assertEqual(second["playerIID"], "player2")
        self.assertNotEqual(first["playerIID"], second["playerIID"])
        self.assertNotEqual(first["accountID"], second["accountID"])
        self.assertNotEqual(first["pgsToken"], second["pgsToken"])
        self.assertNotEqual(first["accounts"][0]["pgsToken"],
                            second["accounts"][0]["pgsToken"])

    def test_identity_is_stable_across_a_session(self):
        server = self._server({
            "GUL_COOKIES": "1",
            "GUL_TRACK_CTX": "1",
            "GUL_TRACK_DELAY": "0",
            "GUL_RCONF": "1",
        })
        token = "C" * 32
        context, headers = self._login(server, token)
        cookie = self._cookie(headers)
        self.assertTrue(cookie.startswith("hns_session="), cookie)

        # The same login id resolves to the same identity on a repeated login.
        again, _ = self._login(server, token)
        self.assertEqual((again["displayName"], again["playerIID"]),
                         (context["displayName"], context["playerIID"]))

        # postTrackingInfo repeats the id as `streamline_session_id`.
        body, _ = _post(server, "/remoting/appserver/postTrackingInfo",
                        _tracking_body(token))
        tracked = base._parse_reply(body).return_val
        self.assertEqual((tracked["displayName"], tracked["playerIID"]),
                         (context["displayName"], context["playerIID"]))

        # A keyless leg resolves the identity from the issued cookie.
        body, _ = _get(server, "/remoting-config", {"Cookie": cookie})
        config = json.loads(body.decode("utf-8"))
        self.assertEqual((config["displayName"], config["playerIID"]),
                         (context["displayName"], context["playerIID"]))
        self.assertEqual(config["accounts"][0]["id"], context["playerIID"])

        body, _ = _get(server, "/parties", {"Cookie": cookie})
        party = json.loads(body.decode("utf-8"))
        self.assertEqual(party["owner"], context["playerIID"])
        self.assertEqual(party["users"][0]["displayName"], context["displayName"])
        self.assertEqual(party["accounts"][0]["id"], context["playerIID"])

        # The user watcher names the player in its own path.
        body, _ = _get(server, "/users/%s/updates/1" % context["playerIID"])
        user = json.loads(body.decode("utf-8"))["users"][0]
        self.assertEqual((user["id"], user["playerIID"], user["displayName"]),
                         (context["playerIID"], context["playerIID"],
                          context["displayName"]))

    def test_two_cookies_keep_two_identities(self):
        server = self._server({"GUL_COOKIES": "1", "GUL_RCONF": "1"})
        first, first_headers = self._login(server, "D" * 32)
        second, second_headers = self._login(server, "E" * 32)
        for context, headers in ((first, first_headers), (second, second_headers)):
            body, _ = _get(server, "/remoting-config", {"Cookie": self._cookie(headers)})
            config = json.loads(body.decode("utf-8"))
            self.assertEqual((config["displayName"], config["playerIID"]),
                             (context["displayName"], context["playerIID"]))
        self.assertNotEqual(first["playerIID"], second["playerIID"])

    def test_explicit_override_pins_one_identity(self):
        server = self._server({"GUL_IID": "alice", "GUL_NAME": "Alice"})
        first, _ = self._login(server, "F" * 32)
        second, _ = self._login(server, "G" * 32)
        for context in (first, second):
            self.assertEqual(context["playerIID"], "alice")
            self.assertEqual(context["displayName"], "Alice")

    def test_name_prefix_is_configurable(self):
        server = self._server({"GUL_NAME_PREFIX": "Bot"})
        first, _ = self._login(server, "H" * 32)
        second, _ = self._login(server, "I" * 32)
        self.assertEqual((first["displayName"], first["playerIID"]), ("Bot1", "bot1"))
        self.assertEqual((second["displayName"], second["playerIID"]), ("Bot2", "bot2"))

    def test_keyless_login_still_gets_its_own_identity(self):
        # No tracking id and no cookie: the login still allocates, and the issued cookie is
        # what carries that identity forward.
        server = self._server({"GUL_COOKIES": "1", "GUL_RCONF": "1"})
        first_body, first_headers = _post(
            server, "/remoting/appserver/startSession", b"__x=nah")
        second_body, second_headers = _post(
            server, "/remoting/appserver/startSession", b"__x=nah")
        first = base._parse_reply(first_body).return_val
        second = base._parse_reply(second_body).return_val
        self.assertNotEqual(first["playerIID"], second["playerIID"])

        body, _ = _get(server, "/remoting-config", {"Cookie": self._cookie(first_headers)})
        config = json.loads(body.decode("utf-8"))
        self.assertEqual(config["playerIID"], first["playerIID"])
        body, _ = _get(server, "/remoting-config", {"Cookie": self._cookie(second_headers)})
        config = json.loads(body.decode("utf-8"))
        self.assertEqual(config["playerIID"], second["playerIID"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
