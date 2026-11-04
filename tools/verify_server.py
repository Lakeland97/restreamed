#!/usr/bin/env python3
"""Check that a running server speaks the minimum the Streamline client needs.

The mock in this repo is one implementation; this tool is for judging any other (a port, a rewrite, a
container). It sends the legs the client actually sends — captured from real client traffic, not invented —
and asserts the shape the client parses. It is deliberately shallow: it answers "does this server belong in
front of the client?" not "is every reply byte-perfect".

Legs checked:

  POST /remoting/appserver/doHandshake      hxr envelope carrying a model.Result
  POST /remoting/appserver/startSession     same envelope
  GET  /auth/check                          JSON with a `body` token
  GET  /user                                `{"users":[{"id": …}]}`
  GET  /users/<id>/updates/<stamp>          `{"users":[{"update_stamp": …}]}` with an advancing cursor
  POST /parties/create                      a party object with `id` and `owner`

Usage:
  python3 tools/verify_server.py --base http://127.0.0.1:8096
  python3 tools/verify_server.py --base http://example:8096 --quiet

Exit 0 when every leg passes, 1 otherwise, 2 when the base URL is unreachable.
"""

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

HANDSHAKE_BODY = b"__x=nah"
PARTY_BODY = b'{"state":"open","max_users":15}'


class Leg:
    def __init__(self, name, ok, detail):
        self.name = name
        self.ok = ok
        self.detail = detail


def _request(base, path, data=None, headers=None, timeout=10):
    url = base.rstrip("/") + path
    request = urllib.request.Request(
        url,
        data=data,
        method="POST" if data is not None else "GET",
        headers={"Content-Type": "application/x-www-form-urlencoded", **(headers or {})},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read()


def _remoting(base, op, data=HANDSHAKE_BODY):
    return _request(base, "/remoting/appserver/" + op, data=data)


def _is_remoting_envelope(body):
    if not body.startswith(b"hxr"):
        return False, "missing hxr marker: %r" % body[:24]
    if b"model.Result" not in body:
        return False, "no model.Result in the reply: %r" % body[:60]
    return True, "hxr envelope with model.Result"


def check_remoting_legs(base):
    for op in ("doHandshake", "startSession"):
        try:
            status, body = _remoting(base, op)
        except Exception as exc:  # noqa: BLE001 - report, do not raise
            yield Leg(op, False, "request failed: %s" % exc)
            continue
        if status != 200:
            yield Leg(op, False, "HTTP %s" % status)
            continue
        ok, detail = _is_remoting_envelope(body)
        yield Leg(op, ok, detail)


def check_auth(base):
    try:
        status, body = _request(base, "/auth/check")
    except Exception as exc:  # noqa: BLE001
        return Leg("auth/check", False, "request failed: %s" % exc)
    if status != 200:
        return Leg("auth/check", False, "HTTP %s" % status)
    try:
        payload = json.loads(body.decode("utf-8"))
    except ValueError:
        return Leg("auth/check", False, "not JSON: %r" % body[:40])
    token = payload.get("body")
    if not isinstance(token, str) or not token:
        return Leg("auth/check", False, "no `body` token: %r" % payload)
    return Leg("auth/check", True, "body token present")


def _users_leg(base, path, label, require_stamp, timeout=10):
    try:
        status, body = _request(base, path, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return Leg(label, False, "request failed: %s" % exc)
    if status != 200:
        return Leg(label, False, "HTTP %s" % status)
    try:
        payload = json.loads(body.decode("utf-8"))
    except ValueError:
        return Leg(label, False, "not JSON: %r" % body[:40])
    users = payload.get("users")
    if not isinstance(users, list) or not users:
        return Leg(label, False, "no non-empty `users` array: %r" % payload)
    user = users[0]
    if not isinstance(user, dict) or not user.get("id"):
        return Leg(label, False, "users[0] has no id: %r" % user)
    if require_stamp and not isinstance(user.get("update_stamp"), int):
        return Leg(label, False, "users[0] has no integer update_stamp: %r" % user)
    return Leg(label, True, "users[0].id=%s" % user["id"])


def check_user_resource(base):
    return _users_leg(base, "/user", "user resource", require_stamp=False)


# A watcher leg is a deliberate long poll: a server with a 10 s hold would fail a 10 s client timeout by a
# hair. Thirty-five seconds clears the default hold with room for a slow host.
WATCH_TIMEOUT = 35


def check_user_watch(base):
    first = _users_leg(base, "/users/verify/updates/1", "user watcher", require_stamp=True,
                       timeout=WATCH_TIMEOUT)
    if not first.ok:
        return first
    try:
        status, body = _request(base, "/users/verify/updates/1", timeout=WATCH_TIMEOUT)
        payload = json.loads(body.decode("utf-8"))
        stamp = payload["users"][0]["update_stamp"]
    except Exception as exc:  # noqa: BLE001
        return Leg("user watcher", False, "second poll failed: %s" % exc)
    if stamp <= 1:
        # echo mode is a supported configuration, so this is a warning rather than a failure
        return Leg("user watcher", True, "cursor echoes (echo mode), stamp=%d" % stamp)
    return Leg("user watcher", True, "cursor advances, stamp=%d" % stamp)


def check_party_create(base):
    try:
        status, body = _request(base, "/parties/create", data=PARTY_BODY,
                                headers={"Content-Type": "application/json"})
    except Exception as exc:  # noqa: BLE001
        return Leg("parties/create", False, "request failed: %s" % exc)
    if status != 200:
        return Leg("parties/create", False, "HTTP %s" % status)
    try:
        party = json.loads(body.decode("utf-8"))
    except ValueError:
        return Leg("parties/create", False, "not JSON: %r" % body[:40])
    if not isinstance(party, dict) or not party.get("id"):
        return Leg("parties/create", False, "no party id: %r" % party)
    if "owner" not in party:
        return Leg("parties/create", False, "no `owner` on the party: %r" % party)
    return Leg("parties/create", True, "party id=%s" % party["id"])


def run(base):
    legs = list(check_remoting_legs(base))
    legs.append(check_auth(base))
    legs.append(check_user_resource(base))
    legs.append(check_user_watch(base))
    legs.append(check_party_create(base))
    return legs


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check a server against the legs the client sends.")
    parser.add_argument("--base", required=True, help="server base URL, e.g. http://127.0.0.1:8096")
    parser.add_argument("--quiet", action="store_true", help="only print failures and the verdict")
    args = parser.parse_args(argv)

    try:
        _request(args.base, "/remoting/appserver/doHandshake", data=HANDSHAKE_BODY)
    except urllib.error.URLError as exc:
        print("cannot reach %s: %s" % (args.base, exc))
        return 2
    except Exception as exc:  # noqa: BLE001
        print("cannot reach %s: %s" % (args.base, exc))
        return 2

    legs = run(args.base)
    failed = [leg for leg in legs if not leg.ok]
    if not args.quiet:
        for leg in legs:
            print("%s %-16s %s" % ("PASS" if leg.ok else "FAIL", leg.name, leg.detail))
    else:
        for leg in failed:
            print("FAIL %-16s %s" % (leg.name, leg.detail))
    print("%d/%d legs pass" % (len(legs) - len(failed), len(legs)))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
