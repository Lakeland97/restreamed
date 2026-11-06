#!/usr/bin/env python3
"""Walk the public-plane reply envelope and return the DSUR's `returnVal`.

Every reply on `/remoting` (the XIAppServerPublic plane) passes the client's
`sl.remoting.DataStoreClient.handleResponse` gate before the op callback runs:

    CookieClient.handleResponse  FUN_1408f8050  -> FUN_1411f7b30 (DataStoreClient)
    gate                          FUN_1411f7750
      tag 1 -> __RTDynamicCast(Success payload -> DataStoreUpdateResponse) at 0x1411f77c9
            -> updatedEntities.length < 1 (branch A) at 0x1411f781e
            -> Result.Success([payload+0x20] = returnVal) at 0x1411f7a91..0x1411f7aa1
            -> invoke the op callback

So the wire is `[null echo][Result.Success(DataStoreUpdateResponse{returnVal: ...})]` for
*every* public op, whatever type the callback then casts `returnVal` to. A bare
`Result.Success(<value>)` fails the cast (NULL) and the gate reads `[NULL+0x8]` at
0x1411f781e.

`success_parts` asserts that envelope and returns `(updatedEntities, entityTypeInfo, returnVal)`;
`success_returnval` keeps the branch-A assertion and returns the decoded `returnVal`:
an enum as `{"enum", "index", "args"}`, an object as a dict, `{}` for an empty object.
"""
import os
import sys

TOOLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import hxinflate  # noqa: E402

DSUR = "appserver.remoting.DataStoreUpdateResponse"
DSUR_FIELDS = ["updatedEntities", "entityTypeInfo", "returnVal"]


def success_parts(body: bytes):
    """Assert `[n][Result.Success(DSUR)]` and return `(updatedEntities, entityTypeInfo, returnVal)`."""
    return _success_parts(body)


def success_returnval(body: bytes):
    """Assert `[n][Result.Success(DSUR)]` and return the DSUR's `returnVal`."""
    updated, _type_info, return_val = _success_parts(body)
    if updated != []:
        raise AssertionError("branch A needs updatedEntities == [], got %r" % (updated,))
    return return_val


def _success_parts(body: bytes):
    if not body.startswith(b"hxr"):
        raise AssertionError("reply is not an hxr remoting body: %r" % body[:40])
    text = body[3:].decode("latin-1")
    reader = hxinflate.Reader(text)

    if reader.value() is not None:
        raise AssertionError("reply does not start with the null path echo")

    if reader.s[reader.i] != "w":
        raise AssertionError("expected the model.Result enum at %d, got %r"
                             % (reader.i, reader.s[reader.i:reader.i + 20]))
    reader.i += 1
    if (reader.value(), reader.value()) != ("model.Result", "Success"):
        raise AssertionError("reply is not model.Result.Success")
    if reader.s[reader.i] != ":":
        raise AssertionError("missing ':' before the enum argument count")
    reader.i += 1
    start = reader.i
    while reader.i < len(text) and reader.s[reader.i].isdigit():
        reader.i += 1
    argc = int(reader.s[start:reader.i]) if reader.i > start else 0
    if argc != 1:
        raise AssertionError("model.Result.Success takes 1 argument, reply says %d" % argc)

    payload = reader.value()
    if reader.i != len(text):
        raise AssertionError("unconsumed reply bytes at %d: %r"
                             % (reader.i, text[reader.i:reader.i + 40]))

    if not isinstance(payload, dict) or "decl" not in payload:
        raise AssertionError(
            "Result.Success payload is not a class instance (%r) — the public-plane gate "
            "requires DataStoreUpdateResponse" % (payload,))
    if payload["decl"].name != DSUR:
        raise AssertionError("Success payload is %s, not %s"
                             % (payload["decl"].name, DSUR))
    if payload["decl"].fields != DSUR_FIELDS:
        raise AssertionError("DSUR field list is %r, expected %r"
                             % (payload["decl"].fields, DSUR_FIELDS))
    if len(payload["body"]) != 3:
        raise AssertionError("DSUR body has %d values, expected 3" % len(payload["body"]))
    updated, type_info, return_val = payload["body"]
    return updated, type_info, return_val
