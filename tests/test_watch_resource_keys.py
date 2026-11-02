#!/usr/bin/env python3
"""Every PGS resource watcher gets its own plural reply key.

The client long-polls `<resource>/<id>/updates/<stamp>` once per PGS model it watches
(`ModelWatcher`, one instance per resource), and it parses each reply through that model's generated
accessor: `getFirstUser`, `getFirstParty`, `getFirstChannel`, `getFirstPoll`, `getFirstWager`,
`getFirstBingo` (plus scoreboards and flash sales). The accessor reads the reply object's plural key,
so `parties/p1/updates/5` has to answer `{"parties": [...]}`. A single users-shaped branch answered
`{"users": [...]}` for every resource, i.e. a null model for all watchers except the user one.

Evidence for the key table is the client's own accessor block (`0x140c03600`-`0x140c05600`), where
each resource's plural key is bound to its accessor: `getFirstUser`, `getFirstParty`,
`getFirstChannel`, `getFirstPoll`, `getFirstWager`, `getFirstBingo`.

No game and no exe: the mock runs on an ephemeral port like the other offline tests.
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

# Resource -> reply key. All plural, and several differ from the single resource model the client
# also fetches (`GET /channels` carries live_channels *and* channels).
RESOURCES = ("parties", "channels", "polls", "wagers", "bingos", "flash_sales", "scoreboards")


class ResourceWatcherTest(unittest.TestCase):
    def _server(self, extra_env=None):
        env = {"GUL_USER_WATCH_HOLD": "0"}
        env.update(extra_env or {})
        server = base.MockStartSessionServer(env)
        self.addCleanup(server.close)
        return server

    def _watch(self, server, path):
        url = "http://127.0.0.1:%d%s" % (server.port, path)
        with urllib.request.urlopen(url, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_each_resource_answers_under_its_own_plural_key(self):
        server = self._server()
        for resource in RESOURCES:
            with self.subTest(resource=resource):
                body = self._watch(server, "/%s/x/updates/5" % resource)
                self.assertIn(resource, body,
                              "%s watcher must be answered with a %r array: %r"
                              % (resource, resource, body))
                self.assertNotIn("users", body,
                                 "a %s reply must not be users-shaped: %r" % (resource, body))
                models = body[resource]
                self.assertIsInstance(models, list, body)
                self.assertTrue(models, "an empty array is the watcher-restart shape: %r" % (body,))
                self.assertIsInstance(models[0], dict, body)
                self.assertIn("id", models[0], body)

    def test_the_cursor_advances_for_every_resource(self):
        server = self._server()
        for resource in RESOURCES:
            with self.subTest(resource=resource):
                body = self._watch(server, "/%s/x/updates/5" % resource)
                self.assertEqual(body[resource][0]["update_stamp"], 6, body)

    def test_the_user_watcher_keeps_its_exact_shape(self):
        # The one watcher that already worked: a users array with an update_stamp, nothing keyed by
        # another resource.
        server = self._server()
        body = self._watch(server, "/users/devuser/updates/5")
        self.assertEqual(list(body), ["users"], body)
        self.assertEqual(body["users"][0]["update_stamp"], 6, body)
        self.assertEqual(body["users"][0]["id"], body["users"][0]["playerIID"], body)

    def test_echo_stamp_holds_a_non_user_watcher_too(self):
        server = self._server({"GUL_USER_WATCH_STAMP": "echo"})
        body = self._watch(server, "/parties/p1/updates/9")
        self.assertEqual(body["parties"][0]["update_stamp"], 9, body)

    def test_the_long_poll_hold_bounds_a_non_user_watcher(self):
        # Same storm protection as the user watcher: without the hold a re-poll can spin.
        server = self._server({"GUL_USER_WATCH_HOLD": "0.4"})
        started = time.monotonic()
        self._watch(server, "/channels/c1/updates/1")
        self.assertGreaterEqual(time.monotonic() - started, 0.3)

    def test_an_unrecognised_resource_still_gets_a_users_reply(self):
        # Keeps the pre-existing behaviour for paths this table does not name.
        server = self._server()
        body = self._watch(server, "/widgets/x/updates/5")
        self.assertIn("users", body, body)


if __name__ == "__main__":
    unittest.main()
