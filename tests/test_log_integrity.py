#!/usr/bin/env python3
"""Concurrent requests must not corrupt the mock's own log.

The log is the lab's evidence: the coverage and watcher judges read its markers, and a run is judged by what
is in it. With parallel handlers, two threads can interleave a buffered write and split a marker across two
lines — a judge would then miss a leg without any error being raised.

The test drives many concurrent requests with large bodies (so writes are sizeable) and asserts that every
line in the log still matches one of the writer's own line shapes. A split marker fails as a line that
matches nothing.
"""

import json
import os
import re
import sys
import threading
import unittest
import urllib.parse
import urllib.request

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)

import test_mock_branch_b as base  # noqa: E402

# Every line the mock writes in a request block starts with one of these.
LINE_SHAPES = [
    re.compile(r"^--- mock_appserver "),             # the start-up banner
    re.compile(r"^=== \d\d:\d\d:\d\d (\w+) "),      # request banner
    re.compile(r"^    H [A-Za-z-]+: "),             # a header
    re.compile(r"^    BODY\(\d+\): "),              # the request body
    re.compile(r"^    REQ  "),                      # decoded request
    re.compile(r"^    DECODE "),                    # decoder notes
    re.compile(r"^    IDENTITY "),                  # identity line
    re.compile(r"^    \("),                         # a handler marker
    re.compile(r"^    REPLY "),                     # reply bytes
    re.compile(r"^    [A-Z][A-Za-z ]*$"),           # bare continuation notes
]


class LogIntegrityTest(unittest.TestCase):
    def _server(self):
        server = base.MockStartSessionServer({"GUL_USER_WATCH_HOLD": "0"})
        self.addCleanup(server.close)
        return server

    def _one(self, port, index):
        # A deliberately large body so each write is big enough to be worth interleaving.
        filler = ("%02d" % index) * 200
        body = urllib.parse.urlencode({"__x": "naoy17:trackingSessionIDy32:%sgh" % filler[:32]}).encode()
        request = urllib.request.Request(
            "http://127.0.0.1:%d/remoting/appserver/startSession" % port,
            data=body, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(request, timeout=30) as response:
            response.read()

    def test_a_concurrent_burst_leaves_only_well_formed_lines(self):
        server = self._server()
        threads = [threading.Thread(target=self._one, args=(server.port, i)) for i in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)

        with open(server._log_path, "r", encoding="utf-8", errors="replace") as handle:
            lines = [line.rstrip("\n") for line in handle if line.strip()]

        self.assertGreater(len(lines), 50, "the log looks empty: %r" % lines[:5])
        malformed = [line for line in lines if not any(shape.match(line) for shape in LINE_SHAPES)]
        self.assertEqual(malformed, [], "malformed log line(s): %r" % (malformed[:5],))

    def test_every_request_has_exactly_one_banner_and_one_reply(self):
        server = self._server()
        threads = [threading.Thread(target=self._one, args=(server.port, i)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        with open(server._log_path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
        banners = len(re.findall(r"^=== ", text, re.M))
        replies = len(re.findall(r"^    REPLY ", text, re.M))
        self.assertEqual(banners, 8, "expected 8 request banners, found %d" % banners)
        self.assertEqual(replies, 8, "expected 8 reply lines, found %d" % replies)
        # A glued line would carry two markers of the same kind.
        for line in text.splitlines():
            self.assertLessEqual(line.count("=== "), 1, line)
            self.assertLessEqual(line.count("    REPLY "), 1, line)


if __name__ == "__main__":
    unittest.main()
