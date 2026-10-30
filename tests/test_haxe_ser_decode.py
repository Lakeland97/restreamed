#!/usr/bin/env python3
"""Request-decoding coverage tests for `tools/haxe_ser.py`.

The mock logs every request body through `haxe_ser.unserialize_two` and never uses the result for
routing, so a decode failure is silent evidence loss: for months the log carried `DECODE FAILED`
for every request that contained an enum, which is most of them (R966/R967). These tests pin the
tokens that were missing, using bodies captured from `runtime/mock_appserver.log`.

Acceptance (worker trace R967): mock_appserver.log:25052 must decode to
`[None, [<CharacterGender enum idx 1>, "skintone_three", [], []]]`.
"""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import haxe_ser  # noqa: E402


class RequestDecodeTest(unittest.TestCase):
    def test_enum_by_name_with_serialized_string(self):
        # equipCosmeticItems, captured 12:20:36.
        path, params = haxe_ser.unserialize_two(
            "najy31:model.game.seek.CharacterGender:1:0y14:skintone_threeahahh")
        self.assertIsNone(path)
        self.assertEqual(params[0], {"__enum__": "model.game.seek.CharacterGender",
                                     "index": 1, "args": []})
        self.assertEqual(params[1:], ["skintone_three", [], []])

    def test_enum_with_one_argument(self):
        # postTrackingInfo / doHandshake bodies carry DRMType.FakeID carrying the account string.
        _, params = haxe_ser.unserialize_two(
            "naoy17:trackingSessionIDy32:107F70544DC5B2029BAE59B8F38F17BF"
            "y3:drmjy25:model.service.drm.DRMType:3:1y8:restrea1y11:countryCodey0:gh")
        drm = params[0]["drm"]
        self.assertEqual(drm["index"], 3)
        self.assertEqual(drm["args"], ["restrea1"])

    def test_negative_int(self):
        # Haxe writes negative ints as i-<digits>; the old reader consumed digits only.
        self.assertEqual(haxe_ser.unserialize("i-1"), -1)
        self.assertEqual(haxe_ser.unserialize("i0"), 0)
        self.assertEqual(haxe_ser.unserialize("i42"), 42)


if __name__ == "__main__":
    unittest.main(verbosity=2)
