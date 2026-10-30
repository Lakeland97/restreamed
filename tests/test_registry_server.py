#!/usr/bin/env python3
"""Community registry: list format, heartbeats, expiry and the HTTP surface."""
import json
import os
import sys
import threading
import unittest
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import registry_server as reg  # noqa: E402


class ListFormatTest(unittest.TestCase):
    def test_parse_minimal(self):
        e = reg.parse_line("Alpha|127.0.0.1:7777")
        self.assertEqual(e, {"name": "Alpha", "host": "127.0.0.1", "port": 7777,
                             "players": 0, "max": 0})

    def test_parse_counts_round_trip(self):
        line = "Lab Server|10.0.0.5:7777|3|16"
        e = reg.parse_line(line)
        self.assertEqual((e["players"], e["max"]), (3, 16))
        self.assertEqual(reg.format_line(e), line)

    def test_rejects_garbage(self):
        for bad in ("", "no-separator", "|1.2.3.4:7777", "name|nohost", "name|host:port"):
            self.assertIsNone(reg.parse_line(bad), bad)


class RegistryTest(unittest.TestCase):
    def test_upsert_and_text(self):
        r = reg.Registry()
        r.upsert(reg.parse_line("Alpha|127.0.0.1:7777|1|8"))
        r.upsert(reg.parse_line("Beta|127.0.0.2:7777"))
        self.assertEqual(r.text(), "Alpha|127.0.0.1:7777|1|8\nBeta|127.0.0.2:7777\n")

    def test_upsert_same_address_updates(self):
        r = reg.Registry()
        r.upsert(reg.parse_line("Alpha|127.0.0.1:7777|1|8"))
        r.upsert(reg.parse_line("Alpha|127.0.0.1:7777|4|8"))
        self.assertEqual(len(r.live()), 1)
        self.assertEqual(r.live()[0]["players"], 4)

    def test_expiry_drops_stale(self):
        now = [1000.0]
        r = reg.Registry(ttl=10, clock=lambda: now[0])
        r.upsert(reg.parse_line("Alpha|127.0.0.1:7777"))
        now[0] += 11
        self.assertEqual(r.expire(), 1)
        self.assertEqual(r.live(), [])


class HttpTest(unittest.TestCase):
    def setUp(self):
        self.registry = reg.Registry()
        self.httpd = reg.serve(self.registry, "127.0.0.1", 0, probe_enabled=False)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=3) as r:
            return r.status, r.read().decode()

    def test_get_servers(self):
        self.registry.upsert(reg.parse_line("Alpha|127.0.0.1:7777|2|8"))
        status, body = self.get("/servers.txt")
        self.assertEqual(status, 200)
        self.assertEqual(body, "Alpha|127.0.0.1:7777|2|8\n")

    def test_heartbeat_adds_entry(self):
        payload = json.dumps({"name": "Beta", "host": "127.0.0.9", "port": 7777,
                              "players": 1, "max": 4}).encode()
        req = urllib.request.Request(self.base + "/heartbeat", data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=3) as r:
            self.assertEqual(r.status, 200)
        _, body = self.get("/servers.txt")
        self.assertEqual(body, "Beta|127.0.0.9:7777|1|4\n")

    def test_bad_heartbeat(self):
        req = urllib.request.Request(self.base + "/heartbeat", data=b"{}", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=3)
        self.assertEqual(ctx.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
