#!/usr/bin/env python3
"""Minimal community-server registry for the overlay.

Serves the plain-text list the overlay's browser reads, one server per line:

    name|host:port|players|max

Two ways to keep it honest:

* heartbeat: a server POSTs /heartbeat with its fields; entries older than
  --ttl are dropped.
* probe: the registry TCP-connects to each host:port every --probe seconds and
  drops the ones that refuse.

Stdlib only. Run:

    python3 tools/registry_server.py --list servers.txt --port 8099
"""
import argparse
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def parse_line(line):
    parts = [p.strip() for p in line.strip().split("|")]
    if len(parts) < 2 or not parts[0] or ":" not in parts[1]:
        return None
    host, _, port = parts[1].rpartition(":")
    if not host or not port.isdigit():
        return None
    players = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    maximum = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
    return {"name": parts[0], "host": host, "port": int(port),
            "players": players, "max": maximum}


def format_line(entry):
    base = "%s|%s:%d" % (entry["name"], entry["host"], entry["port"])
    if entry.get("max"):
        return "%s|%d|%d" % (base, entry.get("players", 0), entry["max"])
    return base


class Registry:
    def __init__(self, path=None, ttl=90.0, clock=time.time):
        self.entries = {}
        self.ttl = ttl
        self.clock = clock
        if path:
            self.load(path)

    def key(self, entry):
        return (entry["host"], entry["port"])

    def load(self, path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    entry = parse_line(line)
                    if entry:
                        entry["seen"] = self.clock()
                        self.entries[self.key(entry)] = entry
        except FileNotFoundError:
            pass

    def upsert(self, entry):
        entry = dict(entry)
        entry["seen"] = self.clock()
        self.entries[self.key(entry)] = entry
        return entry

    def expire(self):
        now = self.clock()
        stale = [k for k, e in self.entries.items() if now - e.get("seen", now) > self.ttl]
        for k in stale:
            del self.entries[k]
        return len(stale)

    def drop(self, host, port):
        self.entries.pop((host, port), None)

    def live(self):
        return sorted(self.entries.values(), key=lambda e: e["name"].lower())

    def text(self):
        return "".join(format_line(e) + "\n" for e in self.live())


def probe(entry, timeout=2.0):
    try:
        with socket.create_connection((entry["host"], entry["port"]), timeout=timeout):
            return True
    except OSError:
        return False


def make_handler(registry, probe_enabled):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, body, ctype="text/plain; charset=utf-8"):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.split("?")[0] in ("/servers.txt", "/servers"):
                if probe_enabled:
                    for entry in list(registry.live()):
                        if not probe(entry):
                            registry.drop(entry["host"], entry["port"])
                self._send(200, registry.text())
            else:
                self._send(404, "not found\n")

        def do_POST(self):
            if self.path.split("?")[0] != "/heartbeat":
                self._send(404, "not found\n")
                return
            length = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                entry = parse_line("%s|%s:%s|%s|%s" % (
                    payload.get("name", ""), payload.get("host", ""), payload.get("port", ""),
                    payload.get("players", 0), payload.get("max", 0)))
            except (ValueError, TypeError):
                entry = None
            if not entry:
                self._send(400, "bad heartbeat\n")
                return
            registry.upsert(entry)
            self._send(200, "ok\n")

    return Handler


def serve(registry, host, port, probe_enabled):
    httpd = ThreadingHTTPServer((host, port), make_handler(registry, probe_enabled))
    return httpd


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", default="servers.txt")
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--ttl", type=float, default=90.0)
    ap.add_argument("--probe", action="store_true",
                    help="TCP-probe each entry per request instead of trusting heartbeats")
    args = ap.parse_args(argv)

    registry = Registry(args.list, ttl=args.ttl)
    httpd = serve(registry, args.bind, args.port, args.probe)
    print("registry on %s:%d, %d entries, ttl %.0fs%s"
          % (args.bind, args.port, len(registry.entries), args.ttl,
             " (probe)" if args.probe else ""), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
