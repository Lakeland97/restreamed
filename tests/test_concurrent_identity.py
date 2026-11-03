#!/usr/bin/env python3
"""Concurrent clients must not borrow each other's identity.

The mock serves a LAN test with several clients at once, so its request state cannot live in module-level
globals: `ThreadingHTTPServer` runs each request on its own thread, and any global written per request
(`_RAW_REQ`, the current op) is shared between them. If that state leaks, one client's identity resolution can
read another's body/cookie and answer with the wrong `playerIID` — which in a two-client rig shows up as two
players colliding, not as an error.

These tests drive several logins at the same time and assert each reply describes the identity that request
asked for. They are deliberately repetitive: a single round can pass by luck.
"""

import json
import os
import sys
import threading
import unittest
import urllib.parse
import urllib.request

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)

import test_mock_branch_b as base  # noqa: E402

# Defeat the harness's single-client pins so identity is allocated per session.
AUTO_IDENTITY = {"GUL_IID": "", "GUL_NAME": ""}


def _login_body(token):
    return urllib.parse.urlencode({
        "__x": "naoy17:trackingSessionIDy32:%sgh" % token,
    }).encode()


class ConcurrentIdentityTest(unittest.TestCase):
    def _server(self):
        env = dict(AUTO_IDENTITY)
        server = base.MockStartSessionServer(env)
        self.addCleanup(server.close)
        return server

    def _login(self, port, token):
        request = urllib.request.Request(
            "http://127.0.0.1:%d/remoting/appserver/startSession" % port,
            data=_login_body(token),
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            return base._parse_reply(response.read()).return_val

    def _drive(self, clients=6, rounds=3):
        server = self._server()
        results = {}
        errors = []

        def worker(index):
            token = ("%X" % (0xA5A5A5A5 + index * 7919)).rjust(32, "c")
            try:
                first = self._login(server.port, token)
                seen = [first["playerIID"]]
                for _ in range(rounds):
                    seen.append(self._login(server.port, token)["playerIID"])
                results[index] = (token, seen)
            except Exception as exc:  # noqa: BLE001 - report per worker
                errors.append((index, repr(exc)))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(clients)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        return results, errors

    def test_each_concurrent_login_keeps_its_own_identity(self):
        results, errors = self._drive()
        self.assertEqual(errors, [])
        identities = [seen[0] for _token, seen in results.values()]
        self.assertEqual(len(set(identities)), len(identities),
                         "two concurrent logins were given the same identity: %r" % (identities,))
        for index, (_token, seen) in results.items():
            self.assertEqual(len(set(seen)), 1,
                             "identity changed between rounds for client %d: %r" % (index, seen))

    def test_a_second_client_does_not_disturb_the_first_ones_watcher(self):
        # The user watcher resolves identity from the request too; interleaved polls must not cross.
        server = self._server()
        first = self._login(server.port, "A" * 32)
        iid = first["playerIID"]

        def poll():
            url = "http://127.0.0.1:%d/users/%s/updates/1" % (server.port, iid)
            with urllib.request.urlopen(url, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))

        bodies = []
        threads = [threading.Thread(target=lambda: bodies.append(poll())) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(len(bodies), 6)
        for body in bodies:
            self.assertEqual(body["users"][0]["id"], iid, body)


class RequestConcurrencyTest(unittest.TestCase):
    """Requests run in parallel by default; `GUL_SERIALIZE=1` restores the old handler-wide lock.

    Parallel is what a two-client LAN test needs: a held watcher poll must not stall the other client's legs.
    It is only safe because request state (body, cookie, current op, identity) is per-thread, which the
    isolation and identity tests above pin. The knob exists for a client that turns out to pipeline on one
    socket, where concurrent writes could interleave.
    """

    @staticmethod
    def _measure(env):
        import time as _time

        server = base.MockStartSessionServer(dict(env, GUL_USER_WATCH_HOLD="0.6"))
        try:
            def watch():
                url = "http://127.0.0.1:%d/users/devuser/updates/1" % server.port
                with urllib.request.urlopen(url, timeout=30) as response:
                    response.read()

            watch_thread = threading.Thread(target=watch)
            watch_thread.start()
            _time.sleep(0.1)          # the hold is definitely in progress
            begin = _time.monotonic()
            with urllib.request.urlopen("http://127.0.0.1:%d/auth/check" % server.port, timeout=30) as response:
                response.read()
            waited = _time.monotonic() - begin
            watch_thread.join(30)
            return waited
        finally:
            server.close()

    def test_a_held_poll_does_not_stall_another_leg(self):
        waited = self._measure({})
        self.assertLess(waited, 0.35,
                        "a held watcher poll stalled another leg (%.2fs): requests are being serialised"
                        % waited)

    def test_the_serialize_knob_restores_the_lock(self):
        waited = self._measure({"GUL_SERIALIZE": "1"})
        self.assertGreaterEqual(waited, 0.35,
                                "GUL_SERIALIZE=1 did not serialise (%.2fs)" % waited)


class RequestStateIsolationTest(unittest.TestCase):
    """The mechanism behind the tests above: request state must be per-thread, not a module global.

    With a plain dict this fails deterministically — the second writer's value is what both threads read —
    which is exactly the collision the request-level tests can only catch by luck.
    """

    def test_raw_request_state_is_thread_local(self):
        import time as _time

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        saved_env = dict(os.environ)
        sys.path.insert(0, root)
        try:
            import mock_appserver as mock  # noqa: WPS433 - imported here to keep the env restore close
        except Exception as exc:  # noqa: BLE001
            self.skipTest("cannot import the mock in-process: %s" % exc)
        try:
            barrier = threading.Barrier(2)
            seen = {}

            def worker(name):
                mock._RAW_REQ["body"] = name
                barrier.wait(timeout=10)   # both threads have written before either reads
                _time.sleep(0.01)
                seen[name] = mock._RAW_REQ.get("body")

            threads = [threading.Thread(target=worker, args=(name,)) for name in ("alpha", "beta")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(30)

            self.assertEqual(seen, {"alpha": "alpha", "beta": "beta"},
                             "request state is shared between threads: %r" % (seen,))
        finally:
            os.environ.clear()
            os.environ.update(saved_env)
            sys.path.remove(root)


if __name__ == "__main__":
    unittest.main()
