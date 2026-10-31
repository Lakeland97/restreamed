#!/usr/bin/env python3
"""Unknown-op fallback tests (regression guard).

The mock answers every remoting op it has no shape for with
`Result.Success(<empty DataStoreUpdateResponse>)` instead of a remoting exception, so an
unimplemented op makes the client continue and name what it wanted rather than stop. Most of
the public surface is in that state today (17 of 31+ ops have a shape), which
makes the fallback load-bearing — a regression here would look like "the client stopped at op X"
and cost a probe round.

These tests pin both halves: the reply shape for an unimplemented op, and the fact that an
implemented op does not take the fallback path.
"""

import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)

import test_mock_branch_b as base  # noqa: E402

UNIMPLEMENTED_OP = "openPack"          # named in XIAppServerPublic, no OP_REPLIES entry
IMPLEMENTED_OP = "startSession"


class UnknownOpFallbackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = base.MockStartSessionServer()
        cls.addClassCleanup(cls.server.close)

    def _log(self):
        with open(self.server._log_path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()

    def test_unimplemented_op_gets_a_success_envelope(self):
        body = self.server.call_op(UNIMPLEMENTED_OP).decode("latin-1")
        self.assertTrue(body.startswith("hxr"), body[:10])
        payload = body[3:]
        self.assertIn("model.Result", payload)
        self.assertIn("Success", payload)
        self.assertNotIn("Exception", payload,
                         "an exception makes the client error out")
        self.assertIn("UNKNOWN OP %r" % UNIMPLEMENTED_OP, self._log())

    def test_implemented_op_never_takes_the_fallback(self):
        self.server.call_op(IMPLEMENTED_OP)
        self.assertNotIn("UNKNOWN OP %r" % IMPLEMENTED_OP, self._log())


if __name__ == "__main__":
    unittest.main(verbosity=2)
