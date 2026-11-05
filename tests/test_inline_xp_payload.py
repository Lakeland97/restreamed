#!/usr/bin/env python3
"""The inline-AdvancementComponent payload: the offline lever that would bind the tutorial SKIP control.

The client's `PlayedFirstQuickMatch` is set by the home-UI builder from the **local** AdvancementComponent
(> 1.0 or > 0 on its two progression getters). A server cannot move the session record, but it can serve the
entity graph, and the entity graph is applied field by field by name — so an inline component instance is the
only server-side route.

What can be checked offline is the payload: the right class, the declared field order `[xp, levelEntries,
maxLevel]`, a non-null level list (a null there faults where the flag reads it), and that the knob being unset
changes nothing at all. Whether the client then writes the flag is one run away; the payload is the half that can
be verified offline.
"""

import json
import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)

import test_mock_branch_b as base  # noqa: E402


class InlineXpPayloadTest(unittest.TestCase):
    def _body(self, extra_env):
        # The harness's BASE_ENV enables the spec-aware HX branch, which returns before this one; disable it so
        # the inline-component path is the one under test.
        env = {"GUL_DSUR_B_OPS": "startSession", "GUL_DSUR_HX": ""}
        env.update(extra_env)
        server = base.MockStartSessionServer(env)
        try:
            return server.start_session(timeout=30)
        finally:
            server.close()

    def test_the_knob_puts_an_advancement_component_in_the_stream(self):
        body = self._body({"GUL_DSUR_HX_INLINE_XP": "5"})
        self.assertIn(b"AdvancementComponent", body)
        for field in (b"xp", b"levelEntries", b"maxLevel"):
            self.assertIn(field, body, "the declaration must name %r" % field)

    def test_the_declared_field_order_is_the_one_the_client_reads(self):
        body = self._body({"GUL_DSUR_HX_INLINE_XP": "5"})
        text = body.decode("latin-1")
        self.assertLess(text.find("xp"), text.find("levelEntries"),
                        "xp is declared before levelEntries in the client's own order")
        self.assertLess(text.find("levelEntries"), text.find("maxLevel"))

    def test_the_knob_is_off_by_default(self):
        body = self._body({})
        self.assertNotIn(b"AdvancementComponent", body,
                         "the payload must be unchanged unless the knob is set")

    def test_the_level_list_is_never_null(self):
        # A null level list would be dereferenced where the flag reads it; the payload must carry a list.
        body = self._body({"GUL_DSUR_HX_INLINE_XP": "5"})
        text = body.decode("latin-1")
        index = text.find("levelEntries")
        self.assertGreater(index, 0)
        window = text[index:index + 200]
        self.assertNotIn("levelEntriesn", window)


if __name__ == "__main__":
    unittest.main()
