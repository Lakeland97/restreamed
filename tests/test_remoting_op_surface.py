#!/usr/bin/env python3
"""Every registered remoting op must answer with a parseable `[n][Result…]` envelope.

The op table is the mock's whole remoting surface, and a handler that raises (a missing env var, a bad
literal, a shape assumption) only shows up when the client happens to call that op — usually in a live
run, which is the most expensive place to find out. This test walks the table offline and asserts, for
every entry:

  * HTTP 200,
  * a body that starts with the `hxr` remoting marker,
  * a body that the lab's envelope parser consumes completely (the same parser the branch-B tests use).

Arguments are deliberately minimal (`__x=nah`); the table is written to be tolerant of that, and an op that
needs real arguments is exactly the kind of thing worth knowing about here rather than in a probe.

The op list is read from the source with `ast`, so the test never imports the mock into the test process
(importing it installs the working defaults into `os.environ`).

No client and no game: the mock runs on an ephemeral port.
"""

import ast
import os
import sys
import unittest
import urllib.error
import urllib.request

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)
sys.path.insert(0, TESTS_DIR)

import test_mock_branch_b as base  # noqa: E402


def op_table():
    """The `OP_REPLIES` keys, read without importing the mock."""
    with open(os.path.join(ROOT, "mock_appserver.py"), "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(target, "id", None) == "OP_REPLIES" for target in node.targets):
            return [key.value for key in node.value.keys if isinstance(key, ast.Constant)]
    raise AssertionError("OP_REPLIES not found in mock_appserver.py")


class RemotingOpSurfaceTest(unittest.TestCase):
    def _server(self):
        server = base.MockStartSessionServer({})
        self.addCleanup(server.close)
        return server

    def _post(self, server, op):
        request = urllib.request.Request(
            "http://127.0.0.1:%d/remoting/appserver/%s" % (server.port, op),
            data=b"__x=nah",
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, response.read()

    def test_the_table_is_not_empty(self):
        self.assertGreaterEqual(len(op_table()), 10, op_table())

    def test_every_op_answers_with_a_parseable_envelope(self):
        server = self._server()
        for op in op_table():
            with self.subTest(op=op):
                status, body = self._post(server, op)
                self.assertEqual(status, 200, body[:120])
                self.assertTrue(body.startswith(b"hxr"),
                                "%s answered with %r" % (op, body[:60]))
                base._parse_reply(body)  # asserts the envelope parses and is fully consumed

    def test_the_private_plane_prefix_answers_the_same_way(self):
        # The client addresses private ops as `/remoting/appserver-pvt/<op>`; the mock dispatches on the
        # op segment, so both planes must produce the same parseable envelope.
        server = self._server()
        for plane in ("appserver", "appserver-pvt"):
            with self.subTest(plane=plane):
                request = urllib.request.Request(
                    "http://127.0.0.1:%d/remoting/%s/loadPlayerFromSession" % (server.port, plane),
                    data=b"__x=nah",
                    method="POST",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                with urllib.request.urlopen(request, timeout=10) as response:
                    self.assertEqual(response.status, 200)
                    body = response.read()
                self.assertTrue(body.startswith(b"hxr"), body[:60])
                base._parse_reply(body)

    def test_an_unknown_op_still_answers_with_a_success_envelope(self):
        # The client must be able to continue past an op the server does not know.
        server = self._server()
        status, body = self._post(server, "notARealOp")
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"hxr"), body[:60])
        base._parse_reply(body)


if __name__ == "__main__":
    unittest.main()
