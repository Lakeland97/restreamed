#!/usr/bin/env python3
"""The three op consumers the reply audit left unknown, and what they actually read.

Short version:

  * `checkVersion` — callback `DedicatedServerManager.performDelayedChecks.__l6` 
    (`_hx_run` 0x1405ca630 -> FUN_1405cab40) reads only `Result.index` (tag, +0x1c). On
    Success it discards `params[0]` and forwards the global `DAT_143bc57e0`. Shape irrelevant.
  * `clientHeartbeat` — FUN_1407a8530 and FUN_1407a89c0 read the payload only inside the
    `tag == 0` (Error) branch ("Client heartbeat error: "); `interval_sec` is read by nobody.
  * `getConfig` — FUN_140b631a0 -> FUN_1407adbd0 reads seven named fields off `returnVal`.
    A null `returnVal` dereferences `[0+0x70]` on Success, so the reply must be an object.
  * `giveEndOfMatchRewards` — FUN_140cbf340 reads `returnVal` as an array of
    `{provider, accountID, rewards}` records and reads its length (`[array+8]`) unguarded;
    a non-array payload is an AV, an empty array is the safe minimum.
  * Private plane — PrivateClient overrides only `__Field` (m_sharedSecret, prepareRequest) and
    inherits `handleResponse`, so `/remoting-pvt` replies need the same DSUR envelope.

No client and no game: the mock runs on an ephemeral localhost port.

    python3 tests/test_consumer_gaps.py
"""
import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)

import test_mock_branch_b as base  # noqa: E402
import reply_walk  # noqa: E402

CONFIG_FIELDS = ["hostWaitTimeoutSecs", "hostedDataTtlSecs", "lowCcu",
                 "nohostWaitTimeoutSecs", "publicConfig", "sendMatchData",
                 "shutdownDelaySecs"]
PRIVATE_OPS = ["getCurrentIP", "getServerOptions", "hostedServerHeartbeat",
               "onEndMatch", "hostHasJoined"]


class GetConfigReplyTest(unittest.TestCase):
    """FUN_1407adbd0 reads these names off returnVal; null would AV at [0+0x70]."""

    @classmethod
    def setUpClass(cls):
        cls.server = base.MockStartSessionServer({})
        cls.addClassCleanup(cls.server.close)
        cls.body = cls.server.call_op("getConfig")

    def test_returnval_is_an_object(self):
        value = reply_walk.success_returnval(self.body)
        self.assertIsInstance(value, dict, value)
        self.assertNotIn("decl", value, "returnVal must be a plain object, not a class")

    def test_it_carries_exactly_the_fields_the_consumer_reads(self):
        self.assertEqual(sorted(reply_walk.success_returnval(self.body)), CONFIG_FIELDS)

    def test_public_config_is_the_handshake_object(self):
        # FUN_1407adbd0 hands it straight to FUN_1407ad1f0, the doHandshake result processor.
        public = reply_walk.success_returnval(self.body)["publicConfig"]
        self.assertIsInstance(public, dict, public)
        self.assertIn("gameID", public)

    def test_the_timers_are_numbers_and_the_flags_are_booleans(self):
        value = reply_walk.success_returnval(self.body)
        for name in ("hostedDataTtlSecs", "shutdownDelaySecs", "hostWaitTimeoutSecs",
                     "nohostWaitTimeoutSecs"):
            self.assertIsInstance(value[name], (int, float), name)
        for name in ("lowCcu", "sendMatchData"):
            self.assertIsInstance(value[name], bool, name)


class EndOfMatchRewardsArrayTest(unittest.TestCase):
    """FUN_140cbf340 reads returnVal as an array and dereferences [array+8] unguarded."""

    @classmethod
    def setUpClass(cls):
        cls.server = base.MockStartSessionServer({})
        cls.addClassCleanup(cls.server.close)
        cls.body = cls.server.call_op("giveEndOfMatchRewards")

    def test_returnval_is_an_array_not_an_object(self):
        value = reply_walk.success_returnval(self.body)
        self.assertIsInstance(value, list, value)
        self.assertEqual(value, [])

    def test_the_old_object_payload_is_gone(self):
        self.assertNotIn(b"Success:1og", self.body)


class PrivatePlaneEnvelopeTest(unittest.TestCase):
    """PrivateClient inherits CookieClient.handleResponse, so the DSUR gate applies here too."""

    @classmethod
    def setUpClass(cls):
        cls.server = base.MockStartSessionServer({})
        cls.addClassCleanup(cls.server.close)

    def test_every_private_op_reply_carries_the_datastore_envelope(self):
        for op in PRIVATE_OPS:
            with self.subTest(op=op):
                body = self.server.call_op(op)
                reply_walk.success_returnval(body)   # raises unless the DSUR is there

    def test_no_private_op_still_sends_a_bare_result_success(self):
        for op in PRIVATE_OPS:
            with self.subTest(op=op):
                self.assertNotIn(b"Success:1o",
                                 self.server.call_op(op),
                                 "%s still answers with a bare Success(<object>)" % op)


class NotReadTest(unittest.TestCase):
    """checkVersion and clientHeartbeat consume the tag only; any returnVal is fine."""

    @classmethod
    def setUpClass(cls):
        cls.server = base.MockStartSessionServer({})
        cls.addClassCleanup(cls.server.close)

    def test_check_version_still_delivers_the_gate_envelope(self):
        reply_walk.success_returnval(self.server.call_op("checkVersion"))

    def test_client_heartbeat_payload_is_unread_but_enveloped(self):
        value = reply_walk.success_returnval(self.server.call_op("clientHeartbeat"))
        self.assertIsInstance(value, dict, value)


if __name__ == "__main__":
    unittest.main(verbosity=2)
