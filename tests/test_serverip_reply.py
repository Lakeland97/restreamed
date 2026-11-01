#!/usr/bin/env python3
"""`getServerIp` reply shape (regression guard).

The party/host branch calls `startEnterGame` -> `getServerIp` and its callback `FUN_1406730d0`
switches on the `appserver.api.ServerIpResponse` constructor index at `+0x1c`, reading the
`SIServer` record by name. That cast target is the value the public-plane gate hands it, so the
wire reply is:

    [null echo][Result.Success(DataStoreUpdateResponse{returnVal: ServerIpResponse.…})]

The gate (`FUN_1411f7750`, reached from `CookieClient.handleResponse` FUN_1408f8050 ->
DataStoreClient FUN_1411f7b30) dyn-casts the Success payload to `DataStoreUpdateResponse` at
0x1411f77c9 and re-wraps `returnVal` as `Result.Success` at 0x1411f7a91. A *bare*
`Result.Success(<ServerIpResponse>)` therefore fails the cast and access-violates at
0x1411f781e — the same fault that kept a bare `Success(UrlAction)` away from the processUrl
handler. (An earlier revision of this file pinned the bare form; that was the gate missed.)

Pinned from the client's own metadata:

  * constructor table `0x143b0b680` (16-byte `{len, ptr}` entries, declaration order):
      0 SIServer (ptr 0x142fa8b18), 1 SIHostConnected (0x142fa8b30),
      2 SIHostNotConnected (0x142fa8b48), 3 SINoFreeServers (0x142fa8b68)
  * `SIServer` factory `0x140e71e90`: `[obj+0x18] = 1` -> arity 1 (one payload object)
  * parameterless statics `0x140e72530`: `[obj+0x18] = 0`, `[obj+0x1c] = 1|2|3` -> arity 0

No client and no game: the mock is started on an ephemeral localhost port, like the other
offline tests.

Run directly (pytest is not required):

    python3 tests/test_serverip_reply.py
"""
import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)

import test_mock_branch_b as base  # noqa: E402
import reply_walk  # noqa: E402  (also puts tools/ on sys.path)

ENUM = "appserver.api.ServerIpResponse"
DSUR = reply_walk.DSUR
# Constructor indices, from the client's declaration-order table at 0x143b0b680.
SI_SERVER = 0
SI_HOST_CONNECTED = 1
SI_HOST_NOT_CONNECTED = 2
SI_NO_FREE_SERVERS = 3


def _decode(body: bytes):
    """Walk the envelope and return the ServerIpResponse enum from the DSUR's returnVal."""
    value = reply_walk.success_returnval(body)
    if not isinstance(value, dict) or value.get("enum") != ENUM:
        raise AssertionError("returnVal is not a %s enum: %r" % (ENUM, value))
    return value


class ServerIpReplyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = base.MockStartSessionServer({})
        cls.addClassCleanup(cls.server.close)
        cls.body = cls.server.call_op("getServerIp")

    def test_default_is_the_pinned_enum_form(self):
        value = _decode(self.body)
        self.assertEqual(value["index"], SI_HOST_NOT_CONNECTED)
        self.assertEqual(value["args"], [])

    def test_reply_carries_the_public_plane_datastore_envelope(self):
        # The enum must sit inside the DSUR's returnVal; a bare Success(<enum>) is rejected by
        # the DataStoreClient gate before the callback runs.
        self.assertIn(DSUR.encode(), self.body)

    def test_client_side_gate_is_satisfied(self):
        # Pin the full table so a future edit cannot silently pick an index the client
        # does not know.
        value = _decode(self.body)
        self.assertIn(value["index"],
                      (SI_SERVER, SI_HOST_CONNECTED, SI_HOST_NOT_CONNECTED, SI_NO_FREE_SERVERS))


class ServerIpServerVariantTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = base.MockStartSessionServer({
            "GUL_SERVER_IP": "10.2.3.4",
            "GUL_GAME_PORT": "7788",
            "GUL_JOIN_PARTY": "party-abc",
            "GUL_JOIN_SESSION": "psession-1",
            "GUL_JOIN_GAMESESSION": "gsession-1",
        })
        cls.addClassCleanup(cls.server.close)
        cls.body = cls.server.call_op("getServerIp")

    def test_opt_in_reply_is_siserver_with_the_configured_endpoint(self):
        value = _decode(self.body)
        self.assertEqual(value["index"], SI_SERVER)
        self.assertEqual(len(value["args"]), 1, "SIServer has arity 1 (factory 0x140e71e90)")
        record = value["args"][0]
        self.assertIsInstance(record, dict, record)
        self.assertEqual(record["host"], "10.2.3.4")
        self.assertEqual(record["port"], 7788)
        self.assertEqual(record["party"], "party-abc")
        self.assertEqual(record["playerSession"], "psession-1")
        self.assertEqual(record["gameSession"], "gsession-1")

    def test_siserver_record_uses_the_field_names_the_reader_reads(self):
        # FUN_1406730d0 reads exactly these names off the payload object
        # (playerSession, gameSession, port, host, party; strings 0x142e6a298/0x142e6a2b8).
        record = _decode(self.body)["args"][0]
        self.assertEqual(sorted(record),
                         ["gameSession", "host", "party", "playerSession", "port"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
