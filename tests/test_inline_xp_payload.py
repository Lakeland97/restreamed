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
import re
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)
sys.path.insert(0, TESTS_DIR)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import hxinflate  # noqa: E402
import test_mock_branch_b as base  # noqa: E402


def _type_cache(body):
    """The type-cache blob inside a served body: `Y<len>:` followed by a `ZVER3…` segment."""
    match = re.search(rb"Y(\d+):ZVER3", body)
    assert match, "no type cache in the body"
    length = int(match.group(1))
    start = match.end() - len(b"ZVER3")
    return body[start:start + length].decode("latin-1")


class _env(object):
    """Set/clear environment variables for a block, restoring them afterwards."""

    def __init__(self, **values):
        self.values = values
        self.saved = {}

    def __enter__(self):
        for key, value in self.values.items():
            self.saved[key] = os.environ.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return self

    def __exit__(self, *exc):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return False


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

    def test_the_served_declaration_is_positional_with_three_fields(self):
        """Decode what the client actually receives, not just the text.

        `custom=False` selects the positional branch (`0x140895903`); `custom=True` would dispatch to
        `hxUnserialize`, which `AdvancementComponent` does not register. `base = -1` means the wire declaration
        names no base class, so the reader's name array index starts from the three own fields.
        """
        body = self._body({"GUL_DSUR_HX_INLINE_XP": "5"})
        reader = hxinflate.Reader(_type_cache(body))
        reader.read_typeinfo_loop()
        decls = {decl.name: decl for decl in reader.types}
        adv = decls.get("model.component.advancement.AdvancementComponent")
        self.assertIsNotNone(adv, "the served type cache does not declare the component")
        self.assertFalse(adv.custom, "custom=True would call a hxUnserialize this class does not have")
        self.assertEqual(adv.fields, ["xp", "levelEntries", "maxLevel"])
        self.assertEqual(adv.base, -1, "the wire declaration names no base class")

    def test_the_level_list_carries_a_typed_entry(self):
        """An empty list faults in the client; the payload must ship a real LevelEntry.

        `0x140BD2EB4` sets the entry cursor to null on a zero count and `0x140BD2EF4` then reads `[cursor+8]`, so
        the list has to be non-empty *and* typed: the element's fields live at `+8` (level) and `+0xc`
        (xpRequired), which only a `LevelEntry` instance has.
        """
        body = self._body({"GUL_DSUR_HX_INLINE_XP": "5"})
        reader = hxinflate.Reader(_type_cache(body))
        reader.read_typeinfo_loop()
        decls = {decl.name: decl for decl in reader.types}
        entry = decls.get("model.component.advancement.LevelEntry")
        self.assertIsNotNone(entry, "the payload declares no LevelEntry class")
        self.assertFalse(entry.custom)
        self.assertEqual(entry.fields, ["level", "xpRequired", "rewards"])
        self.assertIn(b"LevelEntry", body)

    def test_the_default_entry_is_present_and_overridable(self):
        """The builder's own seam: an empty list is replaced by one safe entry, and the env var still wins."""
        import mock_appserver
        with _env(GUL_DSUR_HX_INLINE_LEVELS=None):
            self.assertEqual(mock_appserver._inline_level_entries(), [[2, 0, []]])
        with _env(GUL_DSUR_HX_INLINE_LEVELS='[]'):
            self.assertEqual(mock_appserver._inline_level_entries(), [[2, 0, []]])
        with _env(GUL_DSUR_HX_INLINE_LEVELS='[[3, 10, []], {"level": 4, "xpRequired": 20}]'):
            self.assertEqual(mock_appserver._inline_level_entries(),
                             [[3, 10, []], [4, 20, []]])

    def test_a_level_entry_round_trips_with_its_class_and_values(self):
        """The element the payload builds must decode as a LevelEntry with `[level, xpRequired, rewards]`.

        The mock's array element is a nested typed instance; if the serializer emitted it as a plain value the
        client would get an object without the `+8`/`+0xc` ints the level getter reads.
        """
        entry = hxinflate.Decl("model.component.advancement.LevelEntry", custom=False,
                               fields=["level", "xpRequired", "rewards"])
        stream = hxinflate.object_stream(
            hxinflate.Instance(values=[2, 0, []], decl=entry), purpose="game")
        reader = hxinflate.Reader(stream)
        reader.value()                                     # the purpose string
        value = reader.value()
        self.assertEqual(value["decl"].name, "model.component.advancement.LevelEntry")
        self.assertEqual(value["body"], [2, 0, []])

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
