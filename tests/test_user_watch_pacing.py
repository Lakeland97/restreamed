#!/usr/bin/env python3
"""User-watch pacing tests — the lever that stops the Twitch broadcast-check storm (R1020).

`GET {pgs}/users/<id>/updates/<stamp>` is the ModelWatcher long-poll started at `LoggedIn`
(`SLGameInstance.startWatchingUser`). Every *delivered* update runs `LoginGameMode.onUserUpdate`,
which cancels the previous Twitch broadcasting check and issues a new one
(`https://api.twitch.tv/kraken/streams/<user.id>`). When the watcher re-polls faster than the HTTPS
round trip, every check is cancelled and the client logs, several times per 100 ms:

    HttpClient.hx:144: Ignoring successful request because it was cancelled. url=...

That is the R1018 allocation storm. The client has no throttle of its own: with the mock holding the
long-poll for 10 s the same check runs once per 10.1 s (R1018b logs), and with no hold it storms.

These tests pin the two server-side knobs that end it without a client patch and without any network:

  * `GUL_USER_WATCH_STAMP=echo` answers with the *requested* stamp, so no update is delivered and
    `onUserUpdate` never runs. The `users` array must stay non-empty either way — an empty/missing
    array is the separate 10 s watcher-restart error.
  * `GUL_USER_WATCH_HOLD` bounds the re-poll rate at the same time (echo alone would busy-poll).

No client, no game, no VM: the mock runs on an ephemeral port like the other offline tests.
"""

import json
import os
import sys
import time
import unittest
import urllib.request

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)

import test_mock_branch_b as base  # noqa: E402


class UserWatchPacingTest(unittest.TestCase):
    def _watch(self, server, stamp):
        url = "http://127.0.0.1:%d/users/devuser/updates/%d" % (server.port, stamp)
        started = time.monotonic()
        with urllib.request.urlopen(url, timeout=10) as response:
            body = response.read()
        return json.loads(body.decode("utf-8")), time.monotonic() - started

    def _server(self, extra_env):
        server = base.MockStartSessionServer(extra_env)
        self.addCleanup(server.close)
        return server

    def test_advancing_stamp_is_the_default_and_delivers_an_update(self):
        # Default behaviour: the cursor moves, so the client treats this as a new update and runs
        # the per-update work (the broadcast check). This is the storm-feeding mode.
        server = self._server({"GUL_USER_WATCH_HOLD": "0"})
        body, _ = self._watch(server, 5)
        self.assertEqual(body["users"][0]["update_stamp"], 6)

    def test_echo_stamp_delivers_no_update(self):
        # The stop lever: the stamp comes back unchanged, so the watcher has nothing to deliver.
        server = self._server({"GUL_USER_WATCH_HOLD": "0", "GUL_USER_WATCH_STAMP": "echo"})
        body, _ = self._watch(server, 5)
        self.assertEqual(body["users"][0]["update_stamp"], 5,
                         "echo mode must not advance the cursor")

    def test_echo_still_returns_a_non_empty_users_array(self):
        # A missing/empty users array is a different, fatal-to-the-watcher shape (10 s restart loop).
        server = self._server({"GUL_USER_WATCH_HOLD": "0", "GUL_USER_WATCH_STAMP": "echo"})
        body, _ = self._watch(server, 7)
        users = body.get("users")
        self.assertIsInstance(users, list, body)
        self.assertTrue(users, "users must not be empty in echo mode")
        self.assertEqual(users[0]["id"], "devuser")

    def test_hold_bounds_the_re_poll_rate(self):
        # echo alone still busy-polls; the hold is what bounds the loop. 0.4 s is a bound, not the
        # 10 s window that R1017 suspected of parking a GC-registered thread.
        server = self._server({"GUL_USER_WATCH_HOLD": "0.4", "GUL_USER_WATCH_STAMP": "echo"})
        body, elapsed = self._watch(server, 5)
        self.assertEqual(body["users"][0]["update_stamp"], 5)
        self.assertGreaterEqual(elapsed, 0.3, "hold was not applied: %.3fs" % elapsed)

    def test_poll_delay_env_still_sets_the_hold(self):
        # Backwards compatibility: GUL_USER_POLL_DELAY remains the hold when the new name is unset.
        server = self._server({"GUL_USER_POLL_DELAY": "0.3"})
        body, elapsed = self._watch(server, 5)
        self.assertEqual(body["users"][0]["update_stamp"], 6)
        self.assertGreaterEqual(elapsed, 0.2, "GUL_USER_POLL_DELAY was ignored: %.3fs" % elapsed)


if __name__ == "__main__":
    unittest.main()
