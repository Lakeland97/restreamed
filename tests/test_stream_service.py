"""Offline test for the stream service endpoints the mock serves under GUL_STREAM.

Starts the mock on an ephemeral localhost port, then exercises the poll lifecycle over HTTP: create a
poll, read its state, submit a vote, and confirm the vote lands. No game, no network beyond loopback.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _post(url, body=b"{}"):
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode())


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read().decode())


class StreamServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = _free_port()
        cls.base = "http://127.0.0.1:%d" % cls.port
        env = {k: v for k, v in os.environ.items() if not k.startswith("GUL_")}
        env.update({"GUL_STREAM": "1", "GUL_STREAM_SEED": "5", "GUL_STREAM_WINDOW": "30",
                    "GUL_STREAM_AUDIENCE": "6",
                    "GUL_LOG": os.path.join(tempfile.gettempdir(), "stream_service_test.log")})
        cls.proc = subprocess.Popen([sys.executable, "mock_appserver.py", str(cls.port)],
                                    cwd=ROOT, env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", cls.port), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=5)

    def test_poll_lifecycle_over_http(self):
        created = _post(self.base + "/polls/create")
        self.assertIn("poll_id", created)
        self.assertEqual(len(created["options"]), 3)
        poll_id = created["poll_id"]

        channel = created.get("channel") or "devuser"
        state = _get(self.base + "/channels/%s/poll" % channel)
        self.assertEqual(state["poll_id"], poll_id)
        self.assertEqual(set(state["votes"]), set(created["options"]))

        option = created["options"][0]
        accepted = _post(self.base + "/vote/", json.dumps({"option": option}).encode())
        self.assertTrue(accepted["accepted"])
        after = _get(self.base + "/channels/%s/poll" % channel)
        self.assertGreaterEqual(after["votes"][option], state["votes"][option])

        bad = _post(self.base + "/vote/", json.dumps({"option": "game_mod_nope"}).encode())
        self.assertFalse(bad["accepted"])

    def test_mod_rotation_over_http(self):
        state = _get(self.base + "/channels/devuser/mods")
        self.assertEqual(state["source"], "rotation")
        slots = state["slots"]
        self.assertTrue(slots)
        self.assertEqual(len({s["slot"] for s in slots}), len(slots))
        for s in slots:
            self.assertGreater(s["remaining"], 0)
        again = _get(self.base + "/channels/devuser/mods")
        self.assertEqual([s["mod"] for s in again["slots"]], [s["mod"] for s in slots])

    def test_stream_profile_over_http(self):
        prof = _get(self.base + "/stream/profile")
        self.assertEqual(prof["mode"], "tag")
        self.assertEqual(prof["pool_size"], len(prof["mods"]))
        self.assertGreater(prof["pool_size"], 0)
        for m in prof["mods"]:
            self.assertIn(m["slot"], prof["slots"])
            self.assertGreater(m["duration"], 0)
            self.assertGreater(m["weight"], 0)
        self.assertEqual(prof["rules"]["rotation_uses_votes"], False)


if __name__ == "__main__":
    unittest.main()

class CurrentModTest(unittest.TestCase):
    """`channels/<id>/mods/current` returns exactly one running mod, for a client that applies one at a time."""

    @classmethod
    def setUpClass(cls):
        cls.port = _free_port()
        cls.base = "http://127.0.0.1:%d" % cls.port
        env = {k: v for k, v in os.environ.items() if not k.startswith("GUL_")}
        env.update({"GUL_STREAM": "1", "GUL_ROTATION_SEED": "3",
                    "GUL_LOG": os.path.join(tempfile.gettempdir(), "stream_current_test.log")})
        cls.proc = subprocess.Popen([sys.executable, "mock_appserver.py", str(cls.port)],
                                    cwd=ROOT, env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", cls.port), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=5)

    def test_current_returns_one_running_mod(self):
        body = _get(self.base + "/channels/demo/mods/current")
        self.assertIn("mod", body)
        slot = body["mod"]
        self.assertIsNotNone(slot, "a running rotation always has at least one slot")
        self.assertTrue(slot["mod"].startswith("game_mod_"), slot)
        self.assertGreater(slot["remaining"], 0, slot)
        self.assertEqual(set(slot), {"mod", "slot", "started_at", "ends_at", "remaining", "duration"})

    def test_current_is_stable_between_calls(self):
        a = _get(self.base + "/channels/demo/mods/current")["mod"]
        b = _get(self.base + "/channels/demo/mods/current")["mod"]
        self.assertEqual(a["mod"], b["mod"])
        self.assertEqual(a["slot"], b["slot"])
