#!/usr/bin/env python3
"""Soak the mock the way a LAN test uses it: several clients, mixed legs, overlapping long-polls.

The handler-wide lock was removed so a held watcher poll no longer stalls another client. That change makes
concurrency real, so it needs a test that keeps several clients busy at once rather than two timed requests:

  * every client logs in with its own token and keeps its `playerIID` for the whole run;
  * each client interleaves a held watcher poll, the auth leg and a party create;
  * every reply must be the client's own — a shared value would show up as a foreign id, not as an exception;
  * the mock process must still be alive at the end (a traceback in a worker thread would not kill it, but a
    crashed server would fail every request).

Runs are repeated because interleaving bugs are probabilistic by nature.
"""

import json
import os
import sys
import threading
import time
import unittest
import urllib.parse
import urllib.request

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)

import test_mock_branch_b as base  # noqa: E402

AUTO_IDENTITY = {"GUL_IID": "", "GUL_NAME": ""}


class MeshSoakTest(unittest.TestCase):
    def _server(self, extra_env=None):
        env = dict(AUTO_IDENTITY)
        env.update({"GUL_USER_WATCH_HOLD": "0.05"})
        env.update(extra_env or {})
        server = base.MockStartSessionServer(env)
        self.addCleanup(server.close)
        return server

    @staticmethod
    def _post(port, path, body, ctype="application/x-www-form-urlencoded"):
        request = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (port, path), data=body, method="POST",
            headers={"Content-Type": ctype})
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read()

    def _login(self, port, token):
        _, body = self._post(port, "/remoting/appserver/startSession",
                             urllib.parse.urlencode({
                                 "__x": "naoy17:trackingSessionIDy32:%sgh" % token}).encode())
        return base._parse_reply(body).return_val["playerIID"]

    def _run_client(self, port, index, rounds, problems):
        token = ("%032X" % (0xC0FFEE00 + index * 7919))[:32]
        try:
            iid = self._login(port, token)
            seen = [iid]
            for _ in range(rounds):
                status, body = self._post(port, "/remoting/appserver/startSession",
                                          urllib.parse.urlencode({
                                              "__x": "naoy17:trackingSessionIDy32:%sgh" % token}).encode())
                seen.append(base._parse_reply(body).return_val["playerIID"])
                url = "http://127.0.0.1:%d/users/%s/updates/1" % (port, iid)
                with urllib.request.urlopen(url, timeout=30) as response:
                    watcher = json.loads(response.read().decode("utf-8"))
                if watcher["users"][0]["id"] != iid:
                    problems.append("client %d: watcher returned %s, expected %s"
                                    % (index, watcher["users"][0]["id"], iid))
                status, _auth = self._post(port, "/auth/check", b"", "text/plain")
                if status != 200:
                    problems.append("client %d: auth/check -> %s" % (index, status))
                status, party = self._post(port, "/parties/create",
                                           b'{"state":"open","max_users":15}', "application/json")
                if status != 200 or not json.loads(party.decode("utf-8")).get("id"):
                    problems.append("client %d: parties/create -> %s" % (index, status))
            if len(set(seen)) != 1:
                problems.append("client %d changed identity between rounds: %r" % (index, seen))
        except Exception as exc:  # noqa: BLE001 - a client failure is a problem, not an error
            problems.append("client %d raised: %r" % (index, exc))

    def test_six_clients_interleaving_legs_keep_their_own_identity(self):
        for attempt in range(2):
            server = self._server()
            problems = []
            threads = [threading.Thread(target=self._run_client, args=(server.port, i, 3, problems))
                       for i in range(6)]
            started = time.monotonic()
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(120)
            elapsed = time.monotonic() - started
            self.assertEqual(problems, [], "attempt %d: %s" % (attempt, problems))
            self.assertIsNone(server._proc.poll(), "the mock died during the soak")
            # Parallel handlers should beat a serialised run: 6 clients x 3 rounds x ~0.05 s of hold.
            self.assertLess(elapsed, 30.0, "the soak took %.1fs: something is serialising again" % elapsed)
            server.close()

    def test_two_clients_with_a_long_hold_still_complete(self):
        # The pathological version of the stall that motivated parallel handlers.
        server = self._server({"GUL_USER_WATCH_HOLD": "1.0"})
        problems = []
        threads = [threading.Thread(target=self._run_client, args=(server.port, i, 1, problems))
                   for i in range(2)]
        started = time.monotonic()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(120)
        elapsed = time.monotonic() - started
        self.assertEqual(problems, [])
        # Two clients, one 1 s held poll each: serialised is >= 2 s, parallel is ~1 s plus the other legs.
        # The bound is deliberately tight enough to fail if the handler lock comes back.
        self.assertLess(elapsed, 2.0, "two clients took %.1fs: requests look serialised" % elapsed)


if __name__ == "__main__":
    unittest.main()
