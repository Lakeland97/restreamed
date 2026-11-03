#!/usr/bin/env python3
"""Tests for `tools/registry_server.py`, the community-server registry.

The registry is what the overlay's browser reads, so a silent format break would show up as an empty server
list in the game rather than as an error. These tests pin the line format, the TTL, the heartbeat leg, and the
probe behaviour — including that a probe drops a dead entry and keeps a live one.

Stdlib only; no game and no network beyond loopback.
"""

import json
import os
import socket
import sys
import threading
import unittest
import urllib.error
import urllib.request

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)
sys.path.insert(0, os.path.join(ROOT, "tools"))

try:
    import registry_server as reg  # noqa: E402
except ImportError:  # the public tree ships tools/, so this is only a guard
    reg = None


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now


@unittest.skipUnless(reg is not None, "registry_server is not in this tree")
class RegistryModelTest(unittest.TestCase):
    def test_a_line_round_trips(self):
        entry = {"name": "Lab", "host": "10.0.0.5", "port": 7777, "players": 2, "max": 4}
        parsed = reg.parse_line(reg.format_line(entry))
        self.assertEqual(parsed, entry)

    def test_a_line_without_a_max_keeps_the_players_off(self):
        # Old two-field lines are still valid and must not gain a phantom count.
        self.assertEqual(reg.format_line({"name": "Lab", "host": "h", "port": 1, "players": 3, "max": 0}),
                         "Lab|h:1")

    def test_malformed_lines_are_rejected_rather_than_half_parsed(self):
        for line in ("", "no-pipe", "name|nohostport", "name|host:port", "|host:1"):
            with self.subTest(line=line):
                self.assertIsNone(reg.parse_line(line), line)

    def test_ttl_expiry_uses_the_injected_clock(self):
        clock = FakeClock()
        registry = reg.Registry(ttl=10.0, clock=clock)
        registry.upsert({"name": "A", "host": "h", "port": 1})
        clock.now += 5
        self.assertEqual(registry.expire(), 0)
        clock.now += 6
        self.assertEqual(registry.expire(), 1)
        self.assertEqual(registry.live(), [])

    def test_upsert_refreshes_an_existing_endpoint_rather_than_duplicating_it(self):
        clock = FakeClock()
        registry = reg.Registry(ttl=10.0, clock=clock)
        registry.upsert({"name": "A", "host": "h", "port": 1, "players": 1})
        clock.now += 5
        registry.upsert({"name": "A2", "host": "h", "port": 1, "players": 2})
        self.assertEqual(len(registry.live()), 1)
        self.assertEqual(registry.live()[0]["name"], "A2")
        clock.now += 6
        self.assertEqual(registry.expire(), 0, "the refresh must have restarted the TTL")

    def test_the_list_is_sorted_case_insensitively(self):
        registry = reg.Registry()
        for name in ("zeta", "Alpha", "beta"):
            registry.upsert({"name": name, "host": name, "port": 1})
        self.assertEqual([entry["name"] for entry in registry.live()], ["Alpha", "beta", "zeta"])


@unittest.skipUnless(reg is not None, "registry_server is not in this tree")
class RegistryHTTPTest(unittest.TestCase):
    def _serve(self, registry, probe_enabled=False):
        httpd = reg.serve(registry, "127.0.0.1", 0, probe_enabled)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return "http://127.0.0.1:%d" % httpd.server_port

    def _get(self, base, path="/servers.txt"):
        with urllib.request.urlopen(base + path, timeout=10) as response:
            return response.status, response.read().decode("utf-8")

    def _post(self, base, payload, path="/heartbeat"):
        request = urllib.request.Request(
            base + path, data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read().decode("utf-8")

    def test_a_heartbeat_shows_up_in_the_list(self):
        registry = reg.Registry()
        base = self._serve(registry)
        status, body = self._post(base, {"name": "Lab", "host": "127.0.0.1", "port": 7777,
                                         "players": 1, "max": 4})
        self.assertEqual(status, 200)
        self.assertEqual(body.strip(), "ok")
        status, listing = self._get(base)
        self.assertEqual(status, 200)
        self.assertEqual(listing.strip(), "Lab|127.0.0.1:7777|1|4")

    def test_a_bad_heartbeat_is_rejected_not_stored(self):
        registry = reg.Registry()
        base = self._serve(registry)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self._post(base, {"name": "Lab"})
        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(registry.live(), [])

    def test_an_unknown_path_is_404(self):
        base = self._serve(reg.Registry())
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self._get(base, "/nope")
        self.assertEqual(caught.exception.code, 404)

    def test_probing_drops_a_dead_entry_and_keeps_a_live_one(self):
        # One real listener (this test's own socket) and one closed port.
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        self.addCleanup(listener.close)
        live_port = listener.getsockname()[1]

        dead = socket.socket()
        dead.bind(("127.0.0.1", 0))
        dead_port = dead.getsockname()[1]
        dead.close()

        registry = reg.Registry()
        registry.upsert({"name": "Live", "host": "127.0.0.1", "port": live_port})
        registry.upsert({"name": "Dead", "host": "127.0.0.1", "port": dead_port})
        base = self._serve(registry, probe_enabled=True)
        _, listing = self._get(base)
        self.assertIn("Live", listing)
        self.assertNotIn("Dead", listing)
        self.assertEqual([entry["name"] for entry in registry.live()], ["Live"])


if __name__ == "__main__":
    unittest.main()
