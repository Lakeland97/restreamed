#!/usr/bin/env python3
"""`getServerIp` reply shape (regression guard).

The party/host branch calls `startEnterGame` -> `getServerIp` and dyn-casts the Result payload
straight to `appserver.api.ServerIpResponse` (callback `FUN_1406730d0`, switch on the enum
constructor index at +0x1c). A `DataStoreUpdateResponse` cannot be cast to it, so the old
`_dsur(...)` reply ended every party/host entry in `LoginGameMode error`.

Pinned from the client's own metadata:

  * constructor table `0x143b0b680` (16-byte `{len, ptr}` entries, declaration order):
      0 SIServer (ptr 0x142fa8b18), 1 SIHostConnected (0x142fa8b30),
      2 SIHostNotConnected (0x142fa8b48), 3 SINoFreeServers (0x142fa8b68)
  * `SIServer` factory `0x140e71e90`: `[obj+0x18] = 1` -> arity 1 (one payload object)
  * parameterless statics `0x140e72530`: `[obj+0x18] = 0`, `[obj+0x1c] = 1|2|3` -> arity 0

So the wire form is `Result.Success` carrying `j appserver.api.ServerIpResponse : idx : argc args`.

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

import hxinflate  # noqa: E402  (base puts tools/ on sys.path)

ENUM = "appserver.api.ServerIpResponse"
DSUR = "appserver.remoting.DataStoreUpdateResponse"
# Constructor indices, from the client's declaration-order table at 0x143b0b680.
SI_SERVER = 0
SI_HOST_CONNECTED = 1
SI_HOST_NOT_CONNECTED = 2
SI_NO_FREE_SERVERS = 3


def _decode(body: bytes):
    """Walk `[null echo][model.Result.Success(<enum>)]` and return the enum value dict."""
    if not body.startswith(b"hxr"):
        raise AssertionError("reply is not an hxr remoting body: %r" % body[:40])
    text = body[3:].decode("latin-1")
    reader = hxinflate.Reader(text)
    if reader.value() is not None:
        raise AssertionError("reply does not start with the null path echo")
    if reader.s[reader.i] != "w":
        raise AssertionError("expected Result enum at %d, got %r"
                             % (reader.i, reader.s[reader.i:reader.i + 20]))
    reader.i += 1
    if (reader.value(), reader.value()) != ("model.Result", "Success"):
        raise AssertionError("reply is not model.Result.Success")
    if reader.s[reader.i] != ":":
        raise AssertionError("missing ':' before the enum argument count")
    reader.i += 1
    start = reader.i
    while reader.i < len(text) and reader.s[reader.i].isdigit():
        reader.i += 1
    argc = int(reader.s[start:reader.i])
    if argc != 1:
        raise AssertionError("model.Result.Success takes 1 argument, reply says %d" % argc)
    value = reader.value()
    if reader.i != len(text):
        raise AssertionError("unconsumed reply bytes at %d: %r"
                             % (reader.i, text[reader.i:reader.i + 40]))
    return value


class ServerIpReplyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = base.MockStartSessionServer({})
        cls.addClassCleanup(cls.server.close)
        cls.body = cls.server.call_op("getServerIp").decode("latin-1")

    def test_default_is_the_pinned_enum_form(self):
        value = _decode(self.body.encode("latin-1"))
        self.assertIsInstance(value, dict, value)
        self.assertEqual(value["enum"], ENUM)
        self.assertEqual(value["index"], SI_HOST_NOT_CONNECTED)
        self.assertEqual(value["args"], [])

    def test_default_no_longer_carries_a_datastore_update_response(self):
        # The old reply put the payload inside a DSUR, which the client cannot cast to
        # ServerIpResponse. Assert the shape is gone from the wire, not just from the code path.
        self.assertNotIn(DSUR, self.body)
        self.assertNotIn("DataStoreUpdateResponse", self.body)
        self.assertNotIn("returnVal", self.body)

    def test_client_side_gate_is_satisfied(self):
        # The callback reads the constructor index at +0x1c and dyn-casts the payload; any of
        # the four indices is a valid ServerIpResponse. Pin the full table so a future edit
        # cannot silently pick an index the client does not know.
        value = _decode(self.body.encode("latin-1"))
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
        cls.body = cls.server.call_op("getServerIp").decode("latin-1")

    def test_opt_in_reply_is_siserver_with_the_configured_endpoint(self):
        value = _decode(self.body.encode("latin-1"))
        self.assertEqual(value["enum"], ENUM)
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
        record = _decode(self.body.encode("latin-1"))["args"][0]
        self.assertEqual(sorted(record),
                         ["gameSession", "host", "party", "playerSession", "port"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
