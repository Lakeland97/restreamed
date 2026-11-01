#!/usr/bin/env python3
"""Public-plane reply envelope for `getOpenServer` and `giveEndOfMatchRewards`.

Both ops answered with a *bare* `Result.Success(<value>)`, which the client's DataStoreClient
gate rejects before the op callback: `FUN_1411f7750` dyn-casts every Success payload to
`appserver.remoting.DataStoreUpdateResponse` (0x1411f77c9) and reads `[NULL+0x8]` at
0x1411f781e when the cast fails. The public client is a CookieClient (`FUN_1408f7be0` writes the
DataStoreClient vtable via `FUN_1411f6a70`, then the CookieClient one), and
`CookieClient.handleResponse` (`FUN_1408f8050`, tail call at 0x1408f89ec) always routes through
that gate, so the DSUR envelope is required for every `/remoting` reply.

  * `getOpenServer` — callback `FUN_14066cd70` switches on the `OpenServerResponse` index
    (`OSServer` = 0, arity 1) and reads `party`, `playerSession`, `gameSession`, `host`, `port`
    off the record; `host`/`port` are what `connectToServer("host:port", …)` travels to.
  * `giveEndOfMatchRewards` — private-plane method reached on the public proxy by the
    `manager+0x28` -> `manager+0x20` client patch (MATCHOVER_FATAL M1); its reply value is not
    modelled, so the payload stays `{}` and only the envelope is pinned.

No client and no game: the mock runs on an ephemeral localhost port.

    python3 tests/test_public_reply_envelope.py
"""
import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)

import test_mock_branch_b as base  # noqa: E402
import reply_walk  # noqa: E402

OPEN_SERVER = "appserver.api.OpenServerResponse"
OS_SERVER = 0
OS_DISABLED = 1
OS_NO_FREE_SERVERS = 2


class OpenServerReplyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = base.MockStartSessionServer({"GUL_GAME_HOST": "10.9.9.9",
                                                  "GUL_GAME_PORT": "7790"})
        cls.addClassCleanup(cls.server.close)
        cls.body = cls.server.call_op("getOpenServer")

    def test_reply_carries_the_public_plane_datastore_envelope(self):
        self.assertIn(reply_walk.DSUR.encode(), self.body)

    def test_osserver_is_the_returnval_enum(self):
        value = reply_walk.success_returnval(self.body)
        self.assertIsInstance(value, dict, value)
        self.assertEqual(value["enum"], OPEN_SERVER)
        self.assertEqual(value["index"], OS_SERVER)
        self.assertIn(value["index"], (OS_SERVER, OS_DISABLED, OS_NO_FREE_SERVERS))
        self.assertEqual(len(value["args"]), 1, "OSServer has arity 1 (FUN_140e72a20)")

    def test_record_uses_the_field_names_the_callback_reads(self):
        record = reply_walk.success_returnval(self.body)["args"][0]
        self.assertEqual(sorted(record),
                         ["gameSession", "host", "party", "playerSession", "port"])
        self.assertEqual(record["host"], "10.9.9.9")
        self.assertEqual(record["port"], 7790)
        self.assertIsInstance(record["port"], int)


class EndOfMatchRewardsReplyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = base.MockStartSessionServer({})
        cls.addClassCleanup(cls.server.close)
        cls.body = cls.server.call_op("giveEndOfMatchRewards")

    def test_reply_carries_the_public_plane_datastore_envelope(self):
        self.assertIn(reply_walk.DSUR.encode(), self.body)
        # The old reply put an empty object straight into Success: `...Success:1og`.
        self.assertNotIn(b"Success:1og", self.body)

    def test_payload_is_an_empty_array(self):
        # FUN_140cbf340 reads returnVal as an array (Array<Dynamic>, else cpp.VirtualArray) and
        # dereferences [array+8] with no null guard; `{}` left that pointer NULL (see
        # runtime/CONSUMER_GAPS.md §4).
        self.assertEqual(reply_walk.success_returnval(self.body), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
