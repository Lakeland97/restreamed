#!/usr/bin/env python3
"""Login-gate contract tests.

Turns the static conclusions about the login flow into offline assertions against the
mock's own replies. No client and no game: every test drives `mock_appserver.py` on an
ephemeral port and parses what it serves.

Contract under test (all from static trace, see the report):

- T1 `AppServerManager.startPlayerSession`'s completion keys the session by `playerIID`, and
  `LoggedInPlayer.getBlob` uses the entity's `instanceID` as the datastore key, so the two strings
  must be identical or the blob lookup misses.
- T2 the completion reads exactly `playerIID, pgsToken, accounts, displayName, accountID, provider`
  (plus `session`, tolerated null) off the `returnVal` context.
- T3 `provider` must be an `AccountProvider` enum instance (Fake=0, Steam=1, Twitch=2), not
  the string "twitch": the consumer casts it, and a String leaves `LoggedInPlayer+0xa8` null.
  The host-side Reader cannot decode the enum-by-name form, so this is asserted on the wire token.
- T4 the entity's seventh value (`componentHash`) is not the player template hash, so the client
  takes the construct-components path instead of the early return.
- T5 the login path never issues `loadPlayerFromSession`, `getAccountInfo` or the fake-player ops
  (0 requests in the whole runtime log), so the empty player map is not a login blocker.
"""

import os
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)
sys.path.insert(0, TESTS_DIR)

import test_mock_branch_b as base  # noqa: E402  (reuses the mock harness and parsers)

PLAYER_TEMPLATE_HASH = "328fadb2c851af2a4118350bb22021f0756a5a8c"
LOGIN_CONSUMER_FIELDS = ("playerIID", "pgsToken", "accounts", "displayName", "accountID",
                        "provider")
PRIVATE_OPS = ("loadPlayerFromSession", "getAccountInfo", "startSessionForFakePlayerID",
               "loadPlayerFromFakePlayerID")


class LoginGateContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = base.MockStartSessionServer()
        cls.addClassCleanup(cls.server.close)
        cls.raw_reply = cls.server.start_session()
        cls.reply = base._parse_reply(cls.raw_reply)

    def _entity(self):
        _, element = base._parse_element(self.reply.updated_entities[0])
        self.assertEqual(base._element_class_name(element), base.MODEL_ENTITY)
        return element

    # T1 ---------------------------------------------------------------------------------------
    def test_context_player_key_matches_entity_instance_id(self):
        entity = self._entity()
        instance_id = entity["body"][0]
        self.assertEqual(instance_id, "devuser")
        # getBlob keys the datastore by the entity's instanceID; startPlayerSession keys the
        # session by the context's playerIID. A mismatch silently misses the blob.
        self.assertEqual(self.reply.return_val.get("playerIID"), instance_id)

    # T2 ---------------------------------------------------------------------------------------
    def test_context_carries_every_name_the_login_consumer_reads(self):
        context = self.reply.return_val
        for name in LOGIN_CONSUMER_FIELDS:
            with self.subTest(field=name):
                self.assertIn(name, context, "login completion reads %r" % name)
        self.assertTrue(context.get("displayName"))
        self.assertTrue(context.get("accountID"))
        self.assertIsInstance(context.get("accounts"), list)
        self.assertTrue(context.get("accounts"), "an empty accounts array breaks the contract")

    # T3 ---------------------------------------------------------------------------------------
    def test_provider_serialises_as_the_account_provider_enum(self):
        server = base.MockStartSessionServer({
            "GUL_CTX_PROVIDER_ENUM": "1",
            "GUL_CTX_PROVIDER_IDX": "2",   # Twitch, per the provider table (Fake=0, Steam=1, Twitch=2)
        })
        self.addCleanup(server.close)
        body = server.start_session().decode("latin-1")
        # Enum-by-name wire form: j <class name> : <constructor index> : <arg count>.
        self.assertIn(
            "jY38:model.component.player.AccountProvider:2:0", body,
            "provider must be the AccountProvider enum (Twitch=2), not a Y-string")

    # T4 ---------------------------------------------------------------------------------------
    def test_entity_component_hash_is_not_the_player_template_hash(self):
        entity = self._entity()
        body = entity["body"]
        # The client's ModelEntity.hxUnserialize reads seven top-level values (the seventh is the
        # componentHash); the mock appends one trailing String (GUL_DSUR_HX_TAIL default), which is
        # the shape every successful run used, so only the seven read fields are asserted here.
        self.assertGreaterEqual(len(body), 7, body)
        self.assertNotEqual(body[6], PLAYER_TEMPLATE_HASH,
                            "the player hash would take FUN_140896730's early return")
        self.assertEqual(body[1], "player")

    # T5 ---------------------------------------------------------------------------------------
    def test_login_flow_does_not_issue_the_private_player_ops(self):
        with open(self.server._log_path, "r", encoding="utf-8", errors="replace") as handle:
            log = handle.read()
        for op in PRIVATE_OPS:
            with self.subTest(op=op):
                self.assertNotIn(op, log)


if __name__ == "__main__":
    unittest.main(verbosity=2)

class DefaultConfigurationTest(unittest.TestCase):
    def test_a_bare_launch_returns_the_working_login_reply(self):
        """No environment: the server still answers startSession with the datastore entity."""
        import subprocess, sys, time, urllib.request, socket
        env = {k: v for k, v in os.environ.items() if not k.startswith("GUL_")}
        port = 8791
        env["GUL_LOG"] = os.path.join(tempfile.gettempdir(), "mock_defaults.log")
        proc = subprocess.Popen([sys.executable, "mock_appserver.py", str(port)], env=env,
                                cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.time() + 10
            while time.time() < deadline:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.1)
            req = urllib.request.Request(
                "http://127.0.0.1:%d/remoting/appserver/startSession" % port,
                data=b"__x=nah", method="POST")
            body = urllib.request.urlopen(req, timeout=5).read()
        finally:
            proc.terminate()
        self.assertGreater(len(body), 1000, "default reply is the short form: %d bytes" % len(body))
        text = body.decode("latin-1")
        self.assertIn("entityTypeInfo", text)
        self.assertIn("model.entity.ModelEntity", text)
