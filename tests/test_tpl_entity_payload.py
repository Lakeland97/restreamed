#!/usr/bin/env python3
"""Offline contract: a `player`-template entity payload for branch-B `startSession`.

Why this template: `player` is the only fully decoded, login-critical client template
(`templates.tplc` file offset `0x305C1`, length `0x10C8`) and every component it declares
resolves to a registered client class. The template is resolved client-side from
`Generated/templates.tplc`; the payload must therefore carry no template object, only the
`ModelEntity` whose `templateID` names it.

What is pinned here, and where it came from (runtime/TPL_ENTITY_PAYLOAD.md):

  * the five component definitions and their `templateData` arities — decoded from the
    client's own `player` payload; they are identical to the component class boots'
    `__SetField` property names verified in the binary;
  * the three-field `ComponentDefinition` wire order `autoConstruct, name, templateData`
    and the custom (field-less) `ComponentPropertyDefinition` body — the client's own
    compiled type info for `model.tpl.ComponentDefinition` / `...ComponentPropertyDefinition`
    (templates.tplc type-info stream, first three cache entries).

Everything is built with the mock's own serializer modules (`tools/hxinflate.py`,
`tools/haxe_ser.py`) — the same code path `mock_appserver.py` uses — so no HTTP server, no
game, no VM, and no network are involved.

Run directly (pytest is not required):

    python3 tests/test_tpl_entity_payload.py
"""
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import haxe_ser  # noqa: E402
import hxinflate  # noqa: E402

MODEL_ENTITY = "model.entity.ModelEntity"
MODEL_TEMPLATE = "model.tpl.ModelEntityTemplate"
COMPONENT_DEFINITION = "model.tpl.ComponentDefinition"
COMPONENT_PROPERTY_DEFINITION = "model.tpl.ComponentPropertyDefinition"
CDEF_FIELDS = ["autoConstruct", "name", "templateData"]
TEMPLATE_FIELDS = ["componentDefinitions", "componentHash", "params", "templateID"]
PLAYER_TEMPLATE_ID = "player"
PLAYER_COMPONENT_HASH = "328fadb2c851af2a4118350bb22021f0756a5a8c"

# Decoded from the client `player` payload (TPL_PAYLOAD_REPORT.md §3): component class
# name -> (autoConstruct, [(property, value)]) with the property names the classes'
# __SetField overrides accept. Values are representative; arity and names are not.
PLAYER_TEMPLATE = [
    ("model.component.player.PlayerComponent", True, []),
    ("model.component.player.PlayerUpgradeComponent", True, []),
    (
        "model.component.player.PlayerCosmeticsComponent",
        True,
        [
            ("defaultCosmetics", {"HatHair": "item_cos_hair_14",
                                  "Torso": "item_cos_torso_2"}),
            ("requiredSlots", []),
        ],
    ),
    (
        "model.component.inventory.InventoryComponent",
        True,
        [
            ("startingItemEntries", []),
            ("steamItemEntries", {}),
            ("twitchItemEntries", {}),
            ("maxEntities", 100001),
            ("inventoryTypes", []),
            ("maxSizeByType", [10000, 100000, 100]),
            ("startingSoftLimitByType", [10000, 100000, 100]),
        ],
    ),
    (
        "model.component.advancement.AdvancementComponent",
        True,
        [("levelEntries", []), ("maxLevel", 50)],
    ),
]


def _decls():
    """The four declarations the payload needs, in the client asset's own cache order."""
    ent = hxinflate.Decl(MODEL_ENTITY, custom=True)
    tpl = hxinflate.Decl(MODEL_TEMPLATE, custom=False, fields=TEMPLATE_FIELDS)
    cdef = hxinflate.Decl(COMPONENT_DEFINITION, custom=False, fields=CDEF_FIELDS)
    prop = hxinflate.Decl(COMPONENT_PROPERTY_DEFINITION, custom=True, fields=[])
    return ent, tpl, cdef, prop


def _entity_body(iid="devuser"):
    """The seven-value ModelEntity body: fields only, no inline components.

    Slot 7 is the entity's componentHash. `""` deliberately differs from the template's
    `328fadb2…`, so FUN_140896730 constructs the template's five components instead of
    returning early (runtime/PLAYER_COMPONENTS.md §2-§6).
    """
    return [iid, PLAYER_TEMPLATE_ID, {}, None, None, None, ""]


def _build_reply():
    ent, tpl, cdef, prop = _decls()
    blob = hxinflate.type_cache([ent, tpl, cdef, prop], purpose="game")
    element = hxinflate.object_stream(
        hxinflate.Instance(values=_entity_body(), decl=ent), purpose="game")
    # haxe_ser returns the serialized fragment; the transport prefixes it with b"hxr".
    return b"hxr" + haxe_ser.dsur_branch_b(None, [element], blob, ()).encode("latin-1")


def _template_stream():
    """The `player` template as the client asset declares it, serialized standalone.

    ModelEntityTemplate values are (componentDefinitions, componentHash, params, templateID);
    each definition is a three-value ComponentDefinition whose `templateData` is an Array of
    custom two-value ComponentPropertyDefinition bodies.
    """
    ent, tpl, cdef, prop = _decls()
    definitions = {}
    for name, auto_construct, properties in PLAYER_TEMPLATE:
        template_data = [
            hxinflate.Instance(values=[prop_name, prop_value], decl=prop)
            for prop_name, prop_value in properties
        ]
        definitions[name] = hxinflate.Instance(
            values=[auto_construct, name, template_data], decl=cdef)
    instance = hxinflate.Instance(
        values=[definitions, "", {}, PLAYER_TEMPLATE_ID], decl=tpl)
    return hxinflate.object_stream(instance, purpose="game")


def _parse_reply(body):
    """Decode [null echo][Result.Success(<DSUR>)]; mirrors tests/test_mock_branch_b.py."""
    if not body.startswith(b"hxr"):
        raise AssertionError("reply is not an hxr remoting body: %r" % body[:40])
    text = body[3:].decode("latin-1")
    reader = hxinflate.Reader(text)
    if reader.value() is not None:
        raise AssertionError("reply does not start with the null path echo")
    if reader.s[reader.i] != "w":
        raise AssertionError("expected Result enum at %d" % reader.i)
    reader.i += 1
    if (reader.value(), reader.value()) != ("model.Result", "Success"):
        raise AssertionError("expected Result.Success")
    if reader.s[reader.i] != ":":
        raise AssertionError("missing ':' before the enum constructor index")
    reader.i += 1
    start = reader.i
    while reader.i < len(reader.s) and reader.s[reader.i].isdigit():
        reader.i += 1
    if int(reader.s[start:reader.i]) != 1:
        raise AssertionError("expected Success index 1")
    payload = reader.value()
    if reader.i != len(text):
        raise AssertionError("unconsumed reply bytes at %d" % reader.i)
    if payload["decl"].name != "appserver.remoting.DataStoreUpdateResponse":
        raise AssertionError("unexpected payload class %s" % payload["decl"].name)
    updated, type_cache, return_val = payload["body"]
    return updated, type_cache, return_val


def _parse_element(text):
    reader = hxinflate.Reader(text, skip_header=True)
    value = reader.value()
    if reader.i != len(text):
        raise AssertionError("unconsumed element bytes at %d: %r"
                             % (reader.i, text[reader.i:reader.i + 40]))
    return reader.purpose, value


def _parse_type_cache(text):
    reader = hxinflate.Reader(text)
    return reader.purpose, reader.read_typeinfo_loop()


class PlayerTemplateEntityPayloadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reply = _build_reply()
        cls.updated, cls.type_cache, _ = _parse_reply(cls.reply)

    def test_entity_element_framing(self):
        # One element, purpose-prefixed, class-declared, fully consumed.
        self.assertEqual(len(self.updated), 1)
        element = self.updated[0]
        self.assertIsInstance(element, str)
        self.assertTrue(element.startswith("y4:game"), element[:20])
        purpose, value = _parse_element(element)
        self.assertEqual(purpose, "game")
        self.assertIn("decl", value)
        self.assertEqual(value["decl"].name, MODEL_ENTITY)
        self.assertTrue(value["decl"].custom)

    def test_element_is_entity_only_no_template_declaration(self):
        # The client resolves `templateID` from its own template store; a template stream in
        # updatedEntities cannot dyncast to ModelEntity (R815/R928 crash shape).
        element = self.updated[0]
        self.assertNotIn("model.tpl.", element)
        for name, _auto, _props in PLAYER_TEMPLATE:
            self.assertNotIn(name, element)

    def test_entity_body_is_fields_only_seven_values(self):
        element = self.updated[0]
        body = _parse_element(element)[1]["body"]
        self.assertEqual(len(body), 7, body)
        self.assertEqual(body[0], "devuser")
        self.assertEqual(body[1], PLAYER_TEMPLATE_ID)
        self.assertEqual(body[2], {})
        self.assertEqual(body[3:6], [None, None, None])
        # Non-matching componentHash: the client constructs the five template components.
        self.assertEqual(body[6], "")
        self.assertNotEqual(body[6], PLAYER_COMPONENT_HASH)

    def test_type_cache_declares_the_three_field_component_definition(self):
        purpose, declarations = _parse_type_cache(self.type_cache)
        self.assertEqual(purpose, "game")
        by_name = {d.name: d for d in declarations}
        self.assertIn(COMPONENT_DEFINITION, by_name)
        cdef = by_name[COMPONENT_DEFINITION]
        self.assertFalse(cdef.custom)
        # Literal (not via CDEF_FIELDS) so a changed builder constant fails here.
        self.assertEqual(cdef.fields, ["autoConstruct", "name", "templateData"])
        self.assertIn(MODEL_TEMPLATE, by_name)
        self.assertEqual(by_name[MODEL_TEMPLATE].fields,
                         ["componentDefinitions", "componentHash", "params", "templateID"])
        # templateData elements are custom bodies, not declared-field instances.
        self.assertIn(COMPONENT_PROPERTY_DEFINITION, by_name)
        self.assertTrue(by_name[COMPONENT_PROPERTY_DEFINITION].custom)
        self.assertEqual(by_name[COMPONENT_PROPERTY_DEFINITION].fields, [])

    def test_player_component_definitions_use_autoConstruct_name_templateData(self):
        text = _template_stream()
        purpose, value = _parse_element(text)
        self.assertEqual(purpose, "game")
        self.assertEqual(value["decl"].name, MODEL_TEMPLATE)
        body = value["body"]
        self.assertEqual(len(body), 4, body)
        definitions, component_hash, params, template_id = body
        self.assertEqual(template_id, PLAYER_TEMPLATE_ID)
        self.assertEqual(component_hash, "")
        self.assertEqual(params, {})
        self.assertEqual(list(definitions), [name for name, _a, _p in PLAYER_TEMPLATE])
        for name, auto_construct, properties in PLAYER_TEMPLATE:
            with self.subTest(component=name):
                definition = definitions[name]
                self.assertEqual(definition["decl"].name, COMPONENT_DEFINITION)
                self.assertEqual(definition["decl"].fields,
                                 ["autoConstruct", "name", "templateData"])
                # Wire order is the declaration order: autoConstruct, then name, then templateData.
                self.assertEqual(len(definition["body"]), 3, definition["body"])
                self.assertIs(definition["body"][0], auto_construct)
                self.assertEqual(definition["body"][1], name)
                template_data = definition["body"][2]
                self.assertEqual(len(template_data), len(properties))
                for element, (prop_name, _prop_value) in zip(template_data, properties):
                    # Custom ComponentPropertyDefinition body: exactly (property, value).
                    self.assertEqual(element["decl"].name, COMPONENT_PROPERTY_DEFINITION)
                    self.assertTrue(element["decl"].custom)
                    self.assertEqual(element["decl"].fields, [])
                    self.assertEqual(len(element["body"]), 2, element["body"])
                    self.assertEqual(element["body"][0], prop_name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
