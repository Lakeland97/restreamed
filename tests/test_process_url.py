#!/usr/bin/env python3
"""processUrl reply: Result.Success(UrlAction.UJoinGame({...})).

The wire literal was verified against the client contract: tag 0 is UJoinGame, port is a Haxe Int, the session
string becomes ?PlayerSessionID= on the travel URL.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import mock_appserver  # noqa: E402


class ProcessUrlReplyTest(unittest.TestCase):
    def test_default_join_wire(self):
        saved = {k: os.environ.pop(k, None) for k in
                 ("GUL_GAME_HOST", "GUL_GAME_PORT", "GUL_JOIN_SESSION",
                  "GUL_JOIN_PARTY", "GUL_JOIN_GAMESESSION")}
        try:
            self.assertEqual(
                mock_appserver.process_url_join_reply(),
                "nwY12:model.ResultY7:Success:1"
                "VY42:appserver.remoting.DataStoreUpdateResponse:i-1:i0:i0:3:"
                "Y15:updatedEntitiesY14:entityTypeInfoY9:returnVal:"
                "ahY0:jY23:appserver.api.UrlAction:0:1"
                "oY4:hostY9:127.0.0.1Y4:porti7777Y5:partyR6"
                "Y13:playerSessionY8:client-1Y11:gameSessionR6gg",
            )
        finally:
            for key, value in saved.items():
                if value is not None:
                    os.environ[key] = value

    def test_other_actions_use_the_dsur_envelope(self):
        saved = os.environ.get("GUL_URL_ACTION")
        try:
            for kind in ("nogame", "denied"):
                os.environ["GUL_URL_ACTION"] = kind
                wire = mock_appserver.process_url_join_reply()
                self.assertTrue(wire.startswith("nw"), kind)
                self.assertIn("appserver.remoting.DataStoreUpdateResponse", wire, kind)
                self.assertIn("appserver.api.UrlAction", wire, kind)
        finally:
            if saved is None:
                os.environ.pop("GUL_URL_ACTION", None)
            else:
                os.environ["GUL_URL_ACTION"] = saved

    def test_overrides(self):
        saved = {k: os.environ.get(k) for k in
                 ("GUL_GAME_HOST", "GUL_GAME_PORT", "GUL_JOIN_SESSION",
                  "GUL_JOIN_PARTY", "GUL_JOIN_GAMESESSION")}
        try:
            os.environ["GUL_GAME_HOST"] = "10.0.0.2"
            os.environ["GUL_GAME_PORT"] = "7000"
            os.environ["GUL_JOIN_SESSION"] = "s9"
            os.environ["GUL_JOIN_PARTY"] = "p2"
            os.environ["GUL_JOIN_GAMESESSION"] = "g2"
            wire = mock_appserver.process_url_join_reply()
            self.assertIn("hostY8:10.0.0.2", wire)
            self.assertIn("porti7000", wire)
            self.assertIn("playerSessionY2:s9", wire)
            self.assertIn("partyY2:p2", wire)
            self.assertIn("gameSessionY2:g2", wire)
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
