#!/usr/bin/env python3
"""Offline regression: the mock's branch-B `startSession` reply must push entities only.

Live evidence behind this test: an `updatedEntities` element that inflated to
`model.tpl.ModelEntityTemplate` (instead of `model.entity.ModelEntity`) cannot be dyncast to
`model.entity.ModelEntity`, reaches `awaken(null)`, and crashes the client. Branch-B elements are
therefore contractually `ModelEntity` streams; the type cache (`entityTypeInfo`) is the only place a
template declaration may appear, because the client resolves `templateID` from its own store.

The assertions are made against a real mock reply: the mock is started as a subprocess on an
ephemeral localhost port, a fake `__x=nah` call is POSTed to
`/remoting/appserver/startSession`, and the reply is decoded with `tools/hxinflate.py` (the wire
format owner). No game or network access outside 127.0.0.1 is used.

Run directly (pytest is not required):

    python3 tests/test_mock_branch_b.py
"""
import os
import json
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from typing import NamedTuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import hxinflate  # noqa: E402

MOCK = os.path.join(REPO_ROOT, "mock_appserver.py")

MODEL_ENTITY = "model.entity.ModelEntity"
MODEL_TEMPLATE = "model.tpl.ModelEntityTemplate"
COMPONENT_DEFINITION = "model.tpl.ComponentDefinition"
DSUR = "appserver.remoting.DataStoreUpdateResponse"
CDEF_FIELDS = ["autoConstruct", "name", "templateData"]
PLAYER_COMPONENT_HASH = "328fadb2c851af2a4118350bb22021f0756a5a8c"
ACCOUNT_COMPONENT_HASH = "ab4939221e37ec0a4fc6c99fbde9012b529fa951"

BASE_ENV = {
    "GUL_DSUR_B_OPS": "startSession",
    "GUL_DSUR_HX": "1",
    "GUL_DSUR_HX_TEMPLATE": "1",
    "GUL_DSUR_HX_PURPOSE": "game",
    # These tests describe one client. Pin the legacy identity so the per-session
    # allocation (Player1/player1, ...) does not change every fixture's expected ids.
    "GUL_IID": "devuser",
    "GUL_NAME": "DevUser",
    # The user watcher is a deliberate long-poll; tests must not wait out the hold.
    "GUL_USER_POLL_DELAY": "0",
}


class BranchBReply(NamedTuple):
    updated_entities: list
    type_cache: str
    return_val: object


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _terminate(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        if proc.stderr is not None:
            proc.stderr.close()
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
    if proc.stderr is not None:
        proc.stderr.close()


class MockStartSessionServer:
    """The mock, isolated to an ephemeral port and a throwaway log."""

    def __init__(self, extra_env=None):
        self.port = None
        self._proc = None
        self._log_fd, self._log_path = tempfile.mkstemp(prefix="mock_branch_b_", suffix=".log")
        os.close(self._log_fd)
        # Drop inherited GUL_* knobs so the test describes its own run; keep everything else.
        env = {k: v for k, v in os.environ.items() if not k.startswith("GUL_")}
        env.update(BASE_ENV)
        env.update(extra_env or {})
        env["GUL_LOG"] = self._log_path          # keep the repo log untouched
        env["GUL_BIND"] = "127.0.0.1"

        last_error = None
        for _ in range(3):
            port = _free_port()
            proc = subprocess.Popen(
                [sys.executable, MOCK, str(port)],
                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                self._wait_until_ready(proc, port)
            except RuntimeError as exc:
                last_error = exc
                _terminate(proc)
                continue
            self._proc, self.port = proc, port
            break
        else:
            os.unlink(self._log_path)
            raise last_error

    @staticmethod
    def _wait_until_ready(proc, port, timeout=10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
                proc.stderr.close()
                raise RuntimeError("mock exited before listening:\n" + stderr)
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.25).close()
                return
            except OSError:
                time.sleep(0.02)
        raise RuntimeError("mock did not accept connections on 127.0.0.1:%d" % port)

    def start_session(self, timeout=10.0) -> bytes:
        return self.call_op("startSession", timeout=timeout)

    def call_op(self, op, timeout=10.0) -> bytes:
        request = urllib.request.Request(
            "http://127.0.0.1:%d/remoting/appserver/%s" % (self.port, op),
            data=b"__x=nah",
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()

    def close(self):
        if self._proc is not None:
            _terminate(self._proc)
            self._proc = None
        try:
            os.unlink(self._log_path)
        except FileNotFoundError:
            pass


def _parse_reply(body: bytes) -> BranchBReply:
    """Decode [null echo][Result.Success(<DSUR instance>)] and return the DSUR's three fields.

    The outer envelope is haxe.remoting's Haxe serialization; the two String-valued fields inside it
    (updatedEntities elements, entityTypeInfo) are themselves hxInflate streams. Only the tokens this
    envelope actually uses are walked, and every byte must be consumed.
    """
    if not body.startswith(b"hxr"):
        raise AssertionError("reply is not an hxr remoting body: %r" % body[:40])
    text = body[3:].decode("latin-1")
    reader = hxinflate.Reader(text)             # the outer envelope carries no ZVER header
    if reader.value() is not None:
        raise AssertionError("reply does not start with the null path echo")
    if reader.s[reader.i] != "w":
        raise AssertionError("expected Result enum at %d, got %r"
                             % (reader.i, reader.s[reader.i:reader.i + 20]))
    reader.i += 1
    enum_name = reader.value()
    constructor = reader.value()
    if (enum_name, constructor) != ("model.Result", "Success"):
        raise AssertionError("expected Result.Success, got %r.%r" % (enum_name, constructor))
    if reader.s[reader.i] != ":":
        raise AssertionError("missing ':' before the enum constructor index")
    reader.i += 1
    start = reader.i
    while reader.i < len(reader.s) and reader.s[reader.i].isdigit():
        reader.i += 1
    constructor_index = int(reader.s[start:reader.i])
    if constructor_index != 1:                  # model.Result.Success
        raise AssertionError("expected Success index 1, got %d" % constructor_index)

    payload = reader.value()
    if reader.i != len(text):
        raise AssertionError("unconsumed reply bytes at %d: %r"
                             % (reader.i, text[reader.i:reader.i + 40]))
    if not isinstance(payload, dict) or "decl" not in payload:
        raise AssertionError("expected a declared DSUR instance, got %r" % (payload,))
    if payload["decl"].name != DSUR:
        raise AssertionError("expected %s, got %s" % (DSUR, payload["decl"].name))
    if payload["decl"].fields != ["updatedEntities", "entityTypeInfo", "returnVal"]:
        raise AssertionError("unexpected DSUR field order: %r" % (payload["decl"].fields,))
    body_values = payload["body"]
    if len(body_values) != 3:
        raise AssertionError("expected 3 DSUR values, got %d" % len(body_values))
    updated, type_cache, return_val = body_values
    if not isinstance(updated, list) or not all(isinstance(e, str) for e in updated):
        raise AssertionError("updatedEntities is not Array<String>: %r" % (updated,))
    if not isinstance(type_cache, str):
        raise AssertionError("entityTypeInfo is not a String: %r" % (type_cache,))
    return BranchBReply(updated, type_cache, return_val)


def _parse_element(text: str):
    """One updatedEntities element: purpose value then the object, read with skipHeader."""
    reader = hxinflate.Reader(text, skip_header=True)
    value = reader.value()
    if reader.i != len(text):
        raise AssertionError("unconsumed element bytes at %d: %r"
                             % (reader.i, text[reader.i:reader.i + 40]))
    return reader.purpose, value


def _parse_type_cache(text: str):
    reader = hxinflate.Reader(text)
    return reader.purpose, reader.read_typeinfo_loop()


def _element_class_name(value) -> str:
    if "decl" in value:
        return value["decl"].name
    if "type" in value:                          # T<idx> reference into the cache
        return "<type ref %d>" % value["type"]
    raise AssertionError("element is not a class instance: %r" % (value,))


def _http_json(server, path, method="GET"):
    request = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (server.port, path),
        data=b"" if method == "POST" else None,
        method=method,
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


class BranchBStartSessionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = MockStartSessionServer()
        cls.addClassCleanup(cls.server.close)
        cls.reply = _parse_reply(cls.server.start_session())

        cls.template_server = MockStartSessionServer({"GUL_DSUR_HX_TEMPLATE_ELEMENTS": "1"})
        cls.addClassCleanup(cls.template_server.close)
        cls.template_reply = _parse_reply(cls.template_server.start_session())

    def test_reply_is_branch_b_with_the_session_context(self):
        # Branch B is selected by a non-empty updatedEntities; returnVal still carries the context.
        self.assertTrue(self.reply.updated_entities)
        self.assertIsInstance(self.reply.return_val, dict)
        self.assertEqual(self.reply.return_val.get("sessionID"), "s1")
        self.assertEqual(self.reply.return_val.get("playerIID"), "devuser")

    def test_updated_entities_hold_entities_only(self):
        self.assertTrue(self.reply.updated_entities)
        for index, element in enumerate(self.reply.updated_entities):
            with self.subTest(element=index):
                purpose, value = _parse_element(element)
                self.assertEqual(purpose, "game")
                self.assertEqual(_element_class_name(value), MODEL_ENTITY)
                self.assertNotIn("model.tpl.", element)

    def test_element_stream_shape(self):
        for index, element in enumerate(self.reply.updated_entities):
            with self.subTest(element=index):
                # hxinflate.object_stream writes the purpose first: y4:game.
                self.assertTrue(element.startswith("y4:game"), element[:20])
                self.assertIn(MODEL_ENTITY, element)
                purpose, value = _parse_element(element)
                self.assertEqual(purpose, "game")
                self.assertEqual(_element_class_name(value), MODEL_ENTITY)

    def test_component_definition_uses_the_three_field_wire_order(self):
        purpose, declarations = _parse_type_cache(self.reply.type_cache)
        self.assertEqual(purpose, "game")
        by_name = {decl.name: decl for decl in declarations}
        self.assertIn(COMPONENT_DEFINITION, by_name)
        cdef = by_name[COMPONENT_DEFINITION]
        self.assertFalse(cdef.custom)
        self.assertEqual(cdef.fields, CDEF_FIELDS)
        # The template declaration belongs in the type cache; only elements are entity-only.
        self.assertIn(MODEL_TEMPLATE, by_name)

    def test_entity_component_hash_early_return_body(self):
        # The seventh ModelEntity value is componentHash. Sending player's exact hash makes
        # FUN_140896730 return Success before creating components, so no inline component is needed.
        server = MockStartSessionServer({
            "GUL_DSUR_HX_COMPONENT_HASH": PLAYER_COMPONENT_HASH,
            "GUL_DSUR_HX_TAIL": "0",
            "GUL_IID_TEMPLATE": "player",
            "GUL_IID_TEMPLATE_BLOB": "player",
        })
        self.addCleanup(server.close)
        reply = _parse_reply(server.start_session())
        self.assertEqual(len(reply.updated_entities), 1)
        _, entity = _parse_element(reply.updated_entities[0])
        self.assertEqual(_element_class_name(entity), MODEL_ENTITY)
        body = entity["body"]
        self.assertEqual(len(body), 7, body)
        self.assertEqual(body[0], "devuser")
        self.assertEqual(body[1], "player")
        self.assertEqual(body[2], {})
        self.assertEqual(body[3:6], [None, None, None])
        self.assertEqual(body[6], PLAYER_COMPONENT_HASH)

    def test_account_entity_can_ride_with_its_hash(self):
        server = MockStartSessionServer({
            "GUL_DSUR_HX_COMPONENT_HASH": PLAYER_COMPONENT_HASH,
            "GUL_DSUR_HX_TAIL": "0",
            "GUL_DSUR_HX_EXTRA_ENTITIES": (
                '[{"iid":"accounts","template":"account","hash":"%s"}]'
                % ACCOUNT_COMPONENT_HASH),
        })
        self.addCleanup(server.close)
        reply = _parse_reply(server.start_session())
        self.assertEqual(len(reply.updated_entities), 2)
        _, account = _parse_element(reply.updated_entities[1])
        self.assertEqual(_element_class_name(account), MODEL_ENTITY)
        body = account["body"]
        self.assertEqual(len(body), 7, body)
        self.assertEqual(body[0], "accounts")
        self.assertEqual(body[1], "account")
        self.assertEqual(body[6], ACCOUNT_COMPONENT_HASH)

    def test_nonmatching_hash_keeps_seven_values(self):
        # Without a matching hash the client constructs the player-template components. The
        # seventh value must stay empty/null and no trailing value may be appended.
        server = MockStartSessionServer({
            "GUL_DSUR_HX_TAIL": "0",
            "GUL_IID_TEMPLATE": "player",
            "GUL_IID_TEMPLATE_BLOB": "player",
        })
        self.addCleanup(server.close)
        reply = _parse_reply(server.start_session())
        self.assertEqual(len(reply.updated_entities), 1)
        _, entity = _parse_element(reply.updated_entities[0])
        self.assertEqual(_element_class_name(entity), MODEL_ENTITY)
        body = entity["body"]
        self.assertEqual(len(body), 7, body)
        self.assertEqual(body[1], "player")
        self.assertEqual(body[6], "")

    def test_pgs_heartbeat_has_interval_sec(self):
        body = _http_json(self.server, "/games/streamline/active_user", method="POST")
        self.assertIsInstance(body.get("interval_sec"), int)
        self.assertGreater(body["interval_sec"], 0)

    def test_user_watcher_returns_nonempty_users(self):
        body = _http_json(self.server, "/users/devuser/updates/-1")
        users = body.get("users")
        self.assertIsInstance(users, list)
        self.assertTrue(users)
        self.assertIsNotNone(users[0])
        self.assertEqual(users[0].get("id"), "devuser")
        self.assertIn("update_stamp", users[0])
        self.assertIn("active_party", users[0])

    def test_channels_returns_live_channels(self):
        body = _http_json(self.server, "/channels")
        self.assertEqual(body.get("live_channels"), [])

    def test_get_open_server_returns_osserver(self):
        body = self.server.call_op("getOpenServer")
        self.assertTrue(body.startswith(b"hxrnw"), body[:40])
        self.assertIn(b"OpenServerResponse", body)
        self.assertIn(b"OpenServerResponse:0:1", body)
        self.assertIn(b"hostY9:127.0.0.1", body)
        self.assertIn(b"porti7777", body)

    def test_ds_registration_ops_are_served(self):
        for op, needle in (
            ("getServerOptions", b"GameKind"),
            ("hostedServerHeartbeat", b"model.Result"),
            ("getCurrentIP", b"127.0.0.1"),
        ):
            with self.subTest(op=op):
                body = self.server.call_op(op)
                self.assertTrue(body.startswith(b"hxrnw"), body[:40])
                self.assertIn(needle, body)

    def test_template_elements_knob_gates_template_streams(self):
        # Negative control: the only env difference between the two runs is
        # GUL_DSUR_HX_TEMPLATE_ELEMENTS=1, and it is what puts a template stream in the list.
        self.assertFalse(any(MODEL_TEMPLATE in e for e in self.reply.updated_entities))
        self.assertEqual(len(self.template_reply.updated_entities),
                         len(self.reply.updated_entities) + 1)
        first = self.template_reply.updated_entities[0]
        self.assertTrue(first.startswith("y4:game"), first[:20])
        _, value = _parse_element(first)
        self.assertEqual(_element_class_name(value), MODEL_TEMPLATE)
        # The entity element is still there, and still an entity.
        entity_elements = self.template_reply.updated_entities[1:]
        for index, element in enumerate(entity_elements):
            with self.subTest(element=index):
                _, entity = _parse_element(element)
                self.assertEqual(_element_class_name(entity), MODEL_ENTITY)


if __name__ == "__main__":
    unittest.main(verbosity=2)
