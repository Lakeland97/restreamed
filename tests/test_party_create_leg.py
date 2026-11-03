#!/usr/bin/env python3
"""`POST /parties/create` — a leg the client really calls, with the body it really sends.

Captured from the mock's request log (10 occurrences across logged runs):

    POST /parties/create
    Authorization: Bearer x.eyJ1c2VyIjoicGxheWVyMSJ9.z
    {"state":"open","max_users":15}

The reply is a party object. The client's parse of it is not pinned here (its continuation is reached by
closure dispatch), so these tests lock what the *server* promises: a 200 JSON body with the party's
identity, owner and member array, stable across calls, and independent of the bearer value (the mock does
not authenticate, and a missing header must not change the shape).

No game and no exe: the mock runs on an ephemeral port.
"""

import json
import os
import sys
import unittest
import urllib.request

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)

import test_mock_branch_b as base  # noqa: E402

BODY = b'{"state":"open","max_users":15}'
BEARER = "Bearer x.eyJ1c2VyIjoicGxheWVyMSJ9.z"


class PartyCreateLegTest(unittest.TestCase):
    def _server(self, extra_env=None):
        server = base.MockStartSessionServer(extra_env or {})
        self.addCleanup(server.close)
        return server

    def _create(self, server, body=BODY, headers=None):
        request = urllib.request.Request(
            "http://127.0.0.1:%d/parties/create" % server.port,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_the_observed_request_gets_a_party_object(self):
        server = self._server()
        status, party = self._create(server, headers={"Authorization": BEARER})
        self.assertEqual(status, 200)
        self.assertIsInstance(party, dict, party)
        self.assertTrue(party.get("id"), party)
        self.assertIn("owner", party, party)
        self.assertIsInstance(party.get("users"), list, party)

    def test_the_party_id_is_stable_and_matches_the_configured_one(self):
        server = self._server({"GUL_PGS_PARTY_ID": "party-x"})
        _, first = self._create(server)
        _, second = self._create(server)
        self.assertEqual(first["id"], "party-x", first)
        self.assertEqual(first["id"], second["id"])

    def test_a_missing_bearer_does_not_change_the_shape(self):
        # The mock does not authenticate; a shape that depended on the header would be invisible until a
        # client sent none.
        server = self._server()
        _, with_header = self._create(server, headers={"Authorization": BEARER})
        _, without = self._create(server)
        self.assertEqual(sorted(with_header), sorted(without))

    def test_the_create_leg_carries_no_watcher_cursor(self):
        # The watcher legs answer with an `update_stamp`; a create must not, or the client's model could
        # be confused for a long-poll delivery.
        server = self._server()
        _, party = self._create(server)
        self.assertNotIn("update_stamp", party)


if __name__ == "__main__":
    unittest.main()
