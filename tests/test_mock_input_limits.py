#!/usr/bin/env python3
"""Bound the two ways a request can hold resources: a huge body and a silent socket.

Requests run in parallel (`GUL_SERIALIZE=0`), so a stuck handler is a leaked thread and an unbounded
`Content-Length` is unbounded allocation. The client's own bodies are a few hundred bytes and it always finishes
sending, so both limits are far above anything real:

  * `GUL_MAX_BODY` (8 MB) — a larger declared body is refused with 413 before anything is read;
  * `GUL_REQUEST_TIMEOUT` (120 s) — a connection that stalls is dropped, and must exceed the deliberate
    long-poll hold (10 s default) with headroom.

The tests assert the behaviour, not the numbers, except where the number is the contract.
"""

import json
import os
import socket
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)

import test_mock_branch_b as base  # noqa: E402


class InputLimitsTest(unittest.TestCase):
    def _server(self, extra_env=None):
        server = base.MockStartSessionServer(extra_env or {})
        self.addCleanup(server.close)
        return server

    @staticmethod
    def _post(port, path, body):
        request = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (port, path), data=body, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_a_declared_body_over_the_limit_is_refused_with_413(self):
        # Ask for more than the limit without sending it: the refusal must happen on the header, not the data.
        server = self._server({"GUL_MAX_BODY": "1024"})
        connection = socket.create_connection(("127.0.0.1", server.port), timeout=20)
        try:
            connection.sendall(b"POST /remoting/appserver/startSession HTTP/1.0\r\n"
                               b"Content-Length: 5000\r\n\r\n")
            reply = connection.recv(4096)
        finally:
            connection.close()
        self.assertIn(b"413", reply.split(b"\r\n")[0], reply[:80])

    def test_a_negative_content_length_is_a_bad_request(self):
        """`int("-5")` parses, so the length check let it through to `read(-5)`, which raised.

        The result was a traceback and a dropped connection rather than an answer. A negative declaration is
        malformed in the same way a non-numeric one is, so it gets the same 400 — and the server keeps serving.
        """
        server = self._server()
        connection = socket.create_connection(("127.0.0.1", server.port), timeout=20)
        try:
            connection.sendall(b"POST /remoting/appserver/startSession HTTP/1.0\r\n"
                               b"Content-Length: -5\r\n\r\n")
            reply = connection.recv(4096)
        finally:
            connection.close()
        self.assertIn(b"400", reply.split(b"\r\n")[0], reply[:80])
        status, body = self._post(server.port, "/remoting/appserver/startSession", b"__x=nah")
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"hxr"))

    def test_a_body_within_the_limit_is_served(self):
        server = self._server({"GUL_MAX_BODY": "1024"})
        status, body = self._post(server.port, "/remoting/appserver/startSession", b"__x=" + b"A" * 100)
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"hxr"))

    def test_a_stalled_connection_does_not_block_another_client(self):
        # Open a socket, promise a body, send half of it and wait. Another client must still be served.
        server = self._server({"GUL_USER_WATCH_HOLD": "0"})
        stalled = socket.create_connection(("127.0.0.1", server.port), timeout=20)
        try:
            stalled.sendall(b"POST /remoting/appserver/startSession HTTP/1.0\r\n"
                            b"Content-Length: 100\r\n\r\n")
            stalled.sendall(b"__x=na")          # half a body, then silence
            time.sleep(0.3)

            result = {}

            def poll():
                result["reply"] = self._post(server.port, "/remoting/appserver/startSession", b"__x=nah")

            thread = threading.Thread(target=poll)
            thread.start()
            thread.join(30)
            self.assertIn("reply", result, "a normal request never completed while a socket was stalled")
            status, body = result["reply"]
            self.assertEqual(status, 200)
            self.assertTrue(body.startswith(b"hxr"))
        finally:
            stalled.close()

    def test_malformed_requests_do_not_kill_a_handler(self):
        """A bad body or path must be answered, not raise inside a thread.

        The sweep behind this test is what found the negative-Content-Length defect: the mock logs decode
        failures and keeps serving, so the only signal is a 500 or a dropped connection. These cases are the
        corpus that exercised every op with garbage, plus odd methods and paths.
        """
        server = self._server({"GUL_USER_WATCH_HOLD": "0", "GUL_TRACK_DELAY": "0"})
        bodies = [b"__x=", b"__x=Z", b"__x=" + b"a" * 5000, b"__x=y-1:x",
                  b"__x=%ff%fe%00", b"__x=na" + b"z" * 200]
        for op in ("doHandshake", "startSession", "postTrackingInfo", "getOpenServer", "unknownOp"):
            for body in bodies:
                status, _ = self._post(server.port, "/remoting/appserver/" + op, body)
                self.assertNotEqual(status, 500, (op, body[:24]))
        for path in ("/channels//mods", "/channels///mods/current", "/users//updates/",
                     "/polls/create", "/wagers/", "/remoting-config", "/" + "a" * 2000):
            for method in ("GET", "POST"):
                request = urllib.request.Request("http://127.0.0.1:%d%s" % (server.port, path),
                                                 data=b"" if method == "POST" else None, method=method)
                try:
                    with urllib.request.urlopen(request, timeout=20) as response:
                        status = response.status
                except urllib.error.HTTPError as exc:
                    status = exc.code
                self.assertNotEqual(status, 500, path)
        with urllib.request.urlopen("http://127.0.0.1:%d/channels" % server.port, timeout=20) as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b"live_channels", response.read())

    def test_a_burst_of_connections_is_accepted(self):
        """The listen backlog must cover a parallel burst.

        Requests run in parallel and a client opens several legs at once; the stdlib default of 5 pending
        connections reset the overflow (measured 2026-11-09: 3 of 16 and 18 of 64 simultaneous connects, with no
        server-side error). A burst that fits the socket ceiling must all connect, and the server must still serve.
        """
        server = self._server()
        barrier = threading.Barrier(48)
        errors, socks = [], []

        def one():
            try:
                barrier.wait()
                socks.append(socket.create_connection(("127.0.0.1", server.port), timeout=20))
            except OSError as exc:
                errors.append(exc)

        threads = [threading.Thread(target=one) for _ in range(48)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        try:
            self.assertEqual(errors, [], "connections were reset during the burst")
            self.assertEqual(len(socks), 48)
        finally:
            for sock in socks:
                sock.close()
        status, body = self._post(server.port, "/remoting/appserver/startSession", b"__x=nah")
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"hxr"))

    def test_the_limits_are_what_the_contract_says(self):
        # Import in-process to read the handler's own attributes, restoring the environment it sets on import.
        ROOT = os.path.dirname(TESTS_DIR)
        saved = dict(os.environ)
        sys.path.insert(0, ROOT)
        try:
            import mock_appserver as mock  # noqa: WPS433
        finally:
            os.environ.clear()
            os.environ.update(saved)
            sys.path.remove(ROOT)

        self.assertGreaterEqual(mock.Handler.MAX_BODY, 1024 * 1024,
                                "an 8 MB cap keeps the client's sub-kilobyte bodies comfortably inside")
        # The timeout must exceed the deliberate long-poll hold (10 s by default) with headroom, or a held
        # watcher would be killed mid-wait.
        self.assertGreaterEqual(mock.Handler.timeout, 3 * 10)


if __name__ == "__main__":
    unittest.main()
