#!/usr/bin/env python3
"""`getOpenServer` — the three OpenServerResponse branches, offline.

The quickmatch reply callback `FUN_14066cd70` switches on the `OpenServerResponse` index at
+0x1c after the public-plane DataStore gate has re-wrapped the value:

  * `OSServer` (0, arity 1) reads five record fields by reflection (vtable +0x70) and travels:
    `party` (5, 0x142e6a230) -> `PartyManager.setActiveParty` (`FUN_1406b4520`),
    `playerSession` (0xd, 0x142e6a240) -> `SLLocalPlayer+0xc`,
    `gameSession` (0xb, 0x142e6a258) -> `SLLocalPlayer+0xe`,
    `port` (0x142e6a2b8) -> Dynamic->Int (`FUN_14055b750`) -> `host:port`,
    `host` (0x142e6a298) -> Dynamic->String (`FUN_1404c6650`).
  * `OSDisabled` (1, arity 0) -> `FUN_1407a9d20` latches byte +0xc8 = 1, then
    `setError("Result:ServerError", locale+0x12c)`.
  * `OSNoFreeServers` (2, arity 0) -> `setError("Result:ServerError", locale+0x114)`.

Neither error branch travels, so both are reachable with `GUL_OPEN_SERVER` and no game server.
No client and no game: the mock runs on an ephemeral localhost port.

    python3 tests/test_open_server_branches.py
"""
import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)

import test_mock_branch_b as base  # noqa: E402
import reply_walk  # noqa: E402

OPEN_SERVER = "appserver.api.OpenServerResponse"

# The five names the callback looks up, in no significant order (the record is a map).
READ_FIELDS = ["gameSession", "host", "party", "playerSession", "port"]


def _open_server(extra_env):
    server = base.MockStartSessionServer(extra_env)
    try:
        body = server.call_op("getOpenServer")
    finally:
        server.close()
    return body


class OpenServerBranchTest(unittest.TestCase):
    def test_default_is_osserver_with_every_field_the_callback_reads(self):
        value = reply_walk.success_returnval(_open_server({}))
        self.assertEqual((value["enum"], value["index"]), (OPEN_SERVER, 0))
        self.assertEqual(len(value["args"]), 1, "OSServer has arity 1 (FUN_140e72a20)")
        record = value["args"][0]
        self.assertEqual(sorted(record), READ_FIELDS)
        self.assertIsInstance(record["port"], int, "port is Dynamic->Int at FUN_14055b750")
        for name in ("host", "party", "playerSession", "gameSession"):
            self.assertIsInstance(record[name], str, name)

    def test_disabled_is_arity_zero_and_travels_nowhere(self):
        value = reply_walk.success_returnval(
            _open_server({"GUL_OPEN_SERVER": "disabled"}))
        self.assertEqual((value["enum"], value["index"]), (OPEN_SERVER, 1))
        self.assertEqual(value["args"], [], "OSDisabled has arity 0 (FUN_140e72fc0)")

    def test_no_free_servers_is_arity_zero(self):
        value = reply_walk.success_returnval(
            _open_server({"GUL_OPEN_SERVER": "nofree"}))
        self.assertEqual((value["enum"], value["index"]), (OPEN_SERVER, 2))
        self.assertEqual(value["args"], [], "OSNoFreeServers has arity 0 (FUN_140e72fc0)")


if __name__ == "__main__":
    unittest.main()
