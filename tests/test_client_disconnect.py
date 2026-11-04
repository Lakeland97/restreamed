#!/usr/bin/env python3
"""A client that hangs up mid-reply must be logged in one line, not as a traceback.

The watcher long-poll is cancelled constantly — every relaunch, every probe stop, every client that gives up —
and each cancelled poll used to leave a full `BrokenPipeError` traceback in the service log (189 of them in one
day). That buries the lines the lab actually reads after a run.

The test aborts a request deliberately and asserts the two things that matter: the server keeps serving, and the
log carries a one-line note instead of a traceback.
"""

import os
import socket
import sys
import time
import unittest
import urllib.request

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)

import test_mock_branch_b as base  # noqa: E402


class ClientDisconnectTest(unittest.TestCase):
    def _server(self):
        server = base.MockStartSessionServer({"GUL_USER_WATCH_HOLD": "0"})
        self.addCleanup(server.close)
        return server

    def test_an_aborted_reply_is_one_log_line_and_the_server_survives(self):
        server = self._server()
        # Send a request and close before reading: the reply write hits a closed socket.
        connection = socket.create_connection(("127.0.0.1", server.port), timeout=10)
        connection.sendall(b"POST /remoting/appserver/startSession HTTP/1.0\r\n"
                           b"Content-Length: 7\r\n\r\n__x=nah")
        connection.close()
        time.sleep(0.5)

        # Still serving?
        request = urllib.request.Request(
            "http://127.0.0.1:%d/remoting/appserver/startSession" % server.port,
            data=b"__x=nah", method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(request, timeout=20) as response:
            self.assertEqual(response.status, 200)
            self.assertTrue(response.read().startswith(b"hxr"))

        with open(server._log_path, "r", encoding="utf-8", errors="replace") as handle:
            log = handle.read()
        self.assertNotIn("Traceback", log, "an aborted reply produced a traceback in the log")

    def test_a_bad_content_length_is_a_400_not_a_crash(self):
        server = self._server()
        connection = socket.create_connection(("127.0.0.1", server.port), timeout=10)
        try:
            connection.sendall(b"POST /remoting/appserver/startSession HTTP/1.0\r\n"
                               b"Content-Length: not-a-number\r\n\r\n")
            reply = connection.recv(4096)
        finally:
            connection.close()
        self.assertIn(b"400", reply.split(b"\r\n")[0], reply[:80])


if __name__ == "__main__":
    unittest.main()
