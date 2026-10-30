#!/usr/bin/env python3
"""Tracking-reply envelope tests (R785 regression guard, recorded R966).

`postTrackingInfo` is the op the client calls most (910 requests in the runtime log), and it is the
one place a wrong reply is *fatal*: a single-value body made the client's response layer read one
value past the end -> `Invalid char \\0 at position 16` -> `Fatal error` (cf9.dmp, MODLOG R785).

The proven envelope is two values: a null echo, then a `model.Result` enum. These tests pin that
shape and, as a negative control, prove the check discriminates against the known-fatal one-value
form, so a future "simplify the reply" edit cannot pass silently.

No client, no game: the mock runs on an ephemeral port like the other offline tests.
"""

import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)

import test_mock_branch_b as base  # noqa: E402

# A two-value reply is [null echo][model.Result...]; the enum-by-name form starts with 'w'.
NULL_ECHO = "n"
RESULT_ENUM_PREFIX = "wY12:model.Result"
SINGLE_VALUE_PREFIX = "o"


class TrackingReplyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # GUL_TRACK_DELAY defaults to 10 s (the latch experiment); the wire shape does not depend on it.
        cls.server = base.MockStartSessionServer({"GUL_TRACK_DELAY": "0"})
        cls.addClassCleanup(cls.server.close)
        cls.body = cls.server.call_op("postTrackingInfo").decode("latin-1")

    def _payload(self):
        self.assertTrue(self.body.startswith("hxr"), self.body[:10])
        return self.body[3:]

    def test_reply_is_two_values_with_a_result_enum_second(self):
        payload = self._payload()
        self.assertTrue(payload.startswith(NULL_ECHO), payload[:20])
        self.assertTrue(payload[1:].startswith(RESULT_ENUM_PREFIX),
                        "second value must be the model.Result enum: %r" % payload[1:40])
        self.assertIn("Success", payload)

    def test_second_value_is_not_null(self):
        payload = self._payload()
        second = payload[1:]
        self.assertTrue(second)
        self.assertNotEqual(second, "n", "value2 = null was the silent-run shape (R655)")

    def test_the_check_rejects_the_known_fatal_single_value_form(self):
        server = base.MockStartSessionServer({"GUL_TRACK_DELAY": "0", "GUL_TRACK_STYLE": "single"})
        self.addCleanup(server.close)
        payload = server.call_op("postTrackingInfo").decode("latin-1")[3:]
        self.assertTrue(payload.startswith(SINGLE_VALUE_PREFIX), payload[:20])
        self.assertFalse(payload.startswith(NULL_ECHO),
                         "the single-value form must not look like the two-value envelope")


if __name__ == "__main__":
    unittest.main(verbosity=2)
