#!/usr/bin/env python3
"""Mock Streamline appserver — speaks haxe.remoting (`__x` in, `hxr`+serialized out).

Iterate the reply shapes until the client walks the login state machine to the menu.
Every request is logged raw AND decoded (via tools/haxe_ser.py).

Per-client identity
-------------------
One mock can serve several clients (LAN test). Each login gets its own identity, keyed by
data the client sends — never by source IP:

  * the `trackingSessionID` in the `startSession` body, or the `streamline_session_id` the
    client repeats on `postTrackingInfo`; and
  * the `hns_session` cookie the mock issues (`GUL_COOKIES=1`), which carries that identity
    through later keyless legs such as the PGS party and the user watcher.

A keyless `startSession` still gets a fresh identity; with `GUL_COOKIES=1` the cookie on that
reply carries it forward. The identity is stable for the rest of that login: `displayName`,
`playerIID`, `accountID`, `pgsToken`, the `accounts` records and every PGS field come from
the same record.

Knobs (single-client test overrides):

  GUL_NAME_PREFIX  display-name prefix for auto-assigned identities (default `Player`,
                   giving `Player1`/`player1`, `Player2`/`player2`, ...)
  GUL_IID          pin playerIID for every session (disables auto assignment)
  GUL_NAME         pin displayName for every session (pair with GUL_IID)
  GUL_JWT          pin the PGS token for every session
  GUL_COOKIES=1    issue the per-identity `hns_session` cookie
"""
import base64
import hashlib
import json
import os
import sys
import threading
import time
import urllib.parse
import re
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Round 6/R789: any 3-segment JWT with payload {"user":...} passes checkPGSAuthToken (no signature
# check). Keep base64 padding — the client's segment decoder is stricter than a lenient one.
# Working login configuration baked in as defaults. These select the datastore reply the client
# needs to finish login and seed its session; without them /startSession returns a 726-byte reply
# with no entity or template and the client cannot proceed. Set any of them in the environment to
# A/B-test an alternative (see README, "Running the mock").
_WORKING_DEFAULTS = {
    "GUL_BIND": "0.0.0.0",
    "GUL_DSUR_B_OPS": "startSession",
    "GUL_DSUR_HX": "1",
    "GUL_DSUR_HX_PURPOSE": "game",
    "GUL_DSUR_HX_TEMPLATE": "1",
    "GUL_DSUR_HX_TAIL": "0",
    "GUL_HS_MODE": "resultdsur",
    "GUL_CTX_PROVIDER_ENUM": "1",
    "GUL_CTX_PROVIDER_IDX": "2",
    "GUL_USER_POLL_DELAY": "10",
    "GUL_TRACK_DELAY": "0",
    "GUL_M_DRM": "3",
    "GUL_IID_TEMPLATE": "player",
    "GUL_IID_TEMPLATE_BLOB": "player",
    "GUL_URL_ACTION": "join",
    "GUL_TRACK_STYLE": "result",
    "GUL_NOCL": "0",
}
for _name, _value in _WORKING_DEFAULTS.items():
    os.environ.setdefault(_name, _value)

_DEV_JWT = "x." + base64.urlsafe_b64encode(b'{"user":"devuser"}').decode() + ".z"

SESSION_CTX = {
    "sessionID": "s1", "timeOffset": 0, "trackingID": "t1",
    "provider": "twitch", "m_providerString": "twitch",
    # R792: the client reads these names off the session/account records (found by scanning the
    # three functions that reference the "accounts" literal): pgsToken/accounts/displayName/
    # playerIID/accountID/provider on the account record, and appAccountID/appProvider/pgsPlayerID/
    # pgsPlayerToken/playerSessionID/gameSessionID/isHost/lastPlayerLevel/... on the player record.
    # Send the superset: extra fields are harmless on an anon object, missing ones caused
    # "accounts not in the datastore".
    "accounts": [{"provider": "twitch", "id": "devuser", "providerString": "twitch",
                  "accountID": "a1", "displayName": "DevUser", "pgsToken": _DEV_JWT}],
    "playerIID": "devuser", "accountID": "a1", "displayName": "DevUser",
    "pgsToken": _DEV_JWT,
    "appAccountID": "a1", "appProvider": "twitch",
    "pgsPlayerID": "devuser", "pgsPlayerToken": _DEV_JWT,
    "playerSessionID": "s1", "gameSessionID": "g1",
    "isHost": False, "hideHUD": False, "pgsExternalMode": False,
    "extraLoginOptions": {},
    "lastPlayerLevel": 1, "lastXpIntoLevel": 0, "lastXpForNextLevel": 100,
}

# The party payload is one of the few web legs the client fetches, and a party carries its members'
# accounts: GUL_PGS_ACCOUNTS adds the account records under both the plain and the m_ name.
# The party is built per request from the caller's identity (see `pgs_party()` below).
_PGS_ACCOUNTS = os.environ.get("GUL_PGS_ACCOUNTS", "1") == "1"

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
import haxe_ser  # noqa: E402
from haxe_ser import HxEnum  # noqa: E402

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime", "mock_appserver.log")
BASE = os.environ.get("GUL_BASE", "http://127.0.0.1:8096")  # must match the pak URL port

# doHandshake reply — field cluster recovered from the exe's string table (API_SURFACE.md).
HANDSHAKE = {    "api": BASE,
    "privateApi": BASE,
    "configApi": BASE,
    "privateApiKey": "community-key",
    "gameID": "252850",
    # R788: pgsServer/pgsFrontend are consumed by the client's PGS credentials code as a URL
    # *prefix* ("auth/check?state=..."), and unlike the appserver base it does not insert a
    # separator of its own. With a bare host:port the built URL is "...:8096auth/check", libcurl
    # never connects, and the client parks in "checking credentials" with no request on the wire.
    "pgsServer": BASE + "/",
    "pgsFrontend": BASE + "/",
    # Round 23: regions[0] as String for region setup (FUN_14089cc90/FUN_140a5bb60). Tested [] and
    # ["us-east-1"] under a FIXED latch (GUL_STRTOKEN=0, GUL_TRACK_DELAY=10) — BOTH silent. Ruled out
    # as the gate; keep one region for realism (matches the real server, avoids null-region string ops).
    "availableRegions": ["us-east-1"],
    "motd": "Streamline community server",
    "twitchClientId": "",
    "fuelVendorId": "",
    "quickmatchDisabled": False,
    "disableQuickmatch": False,
    "quickmatchMinPlayers": 1,
    "serverEnvironment": os.environ.get("GUL_SERVER_ENV", "local"),
    "clientTtlSecs": 60,
    "hostedAutojoinCooldownSecs": int(os.environ.get("GUL_COOLDOWN", "10")),
    "joinLinksEnabled": False,
    "pgsMaintenance": False,
    # Round 6+: must be a DRMType enum instance, NOT a bool. Table (Round 7, byte-verified):
    # 0=Steam (needs SDK — stalls), 1=Twitch, 2=PGSDev (web auth branch), 3=FakeID = BYPASS.
    # GUL_M_DRM lets fan-out instances test variants.
    # Round 739: DRMType.FakeID DECLARES 1 ARG (the validator message order is reversed —
    # "expected <our arg count>, got <declared arity>" — so "expected 0, got 1" meant WE
    # sent 0 args and FakeID wants 1). Wire: j<name>:3:1<arg value>.
    "m_drm": HxEnum("model.service.drm.DRMType", int(os.environ.get("GUL_M_DRM", "3")),
                    args=[os.environ.get("GUL_M_DRM_ARG", "restrea1")]),
    "lowCcu": False,
    "publicConfig": {},
    "hostedDataTtlSecs": 60,
    "shutdownDelaySecs": 0,
    "hostWaitTimeoutSecs": 60,
    "nohostWaitTimeoutSecs": 60,
}

# GUL_FAKE_PLAYER: the client's own dev path. sl.config.ClientConfigManager has fakePlayerID and
# pgsDevToken fields, and the private appserver API declares startSessionForFakePlayerID /
# loadPlayerFromFakePlayerID, so a session for a fake player may need no server-side content at all.
# Gated so the benign config stays the default.
# GUL_CTX_EXTRA: extra JSON merged into the served session context, and GUL_CTX_DROP: a comma list of
# fields to remove. Used to sweep the session context's shape, since the branch-B elements feed the
# container rather than the session datastore (R864/R865) and the accounts entry therefore has to come
# from the client acting on this config.
if os.environ.get("GUL_CTX_EXTRA"):
    SESSION_CTX.update(json.loads(os.environ["GUL_CTX_EXTRA"]))
# The context's `provider` (and each account record's) is the enum model.component.player.AccountProvider,
# whose constructors are Fake and Twitch - the field table for the context lists provider among its named
# fields, and the client casts it to that enum type. Sending the string "twitch" fails that cast.
if os.environ.get("GUL_CTX_PROVIDER_ENUM"):
    _prov = HxEnum("model.component.player.AccountProvider",
                   int(os.environ.get("GUL_CTX_PROVIDER_IDX", "1")), [])
    SESSION_CTX["provider"] = _prov
    for _acct in SESSION_CTX.get("accounts", []):
        if isinstance(_acct, dict):
            _acct["provider"] = _prov

# The client reads config fields by name (it reads "drm" exactly that way), and the session-context
# accessors are among the names it looks up. Merging the context's fields into the served config tests
# whether the context is built from the config rather than from an op's reply value.
# The context builder (FUN_140d36830) reads the context's fields under the names trackingID,
# displayName, accountID, playerIID, timeOffset, m_providerString, sessionID and **m_accounts** - the
# account array is `m_accounts`, while every mock reply has sent it as `accounts`. So the builder reads
# null for the accounts and never creates the blob the getter then asks the datastore for
# ("accounts not in the datastore"). GUL_CTX_M_ACCOUNTS mirrors the array under that name.
if os.environ.get("GUL_CTX_M_ACCOUNTS", "1") == "1":
    SESSION_CTX["m_accounts"] = SESSION_CTX.get("accounts", [])

# The context builder FUN_140d36830 names exactly the fields it wants - trackingID, displayName,
# accountID, playerIID, timeOffset, m_providerString, sessionID, m_accounts - while the mock's context also
# carries the *player record*'s fields (appAccountID, pgsPlayerID, playerSessionID, isHost, hideHUD, ...),
# which belong to a different object. GUL_CTX_MINIMAL keeps only the builder's own list, on the theory that
# the desynchronisation comes from a value or field the context should not have.
if os.environ.get("GUL_CTX_MINIMAL") == "1":
    keep = ("trackingID", "displayName", "accountID", "playerIID", "timeOffset",
            "m_providerString", "sessionID", "m_accounts")
    for _k in list(SESSION_CTX):
        if _k not in keep:
            SESSION_CTX.pop(_k, None)

if os.environ.get("GUL_CTX_INTO_CONFIG"):
    for _k, _v in SESSION_CTX.items():
        HANDSHAKE.setdefault(_k, _v)

if os.environ.get("GUL_CTX_DROP"):
    for _f in os.environ["GUL_CTX_DROP"].split(","):
        SESSION_CTX.pop(_f.strip(), None)

# ---------------------------------------------------------------------------
# Per-session identity
#
# The mock serves several clients from one process. A single global `devuser` made every
# client the same player (and collided in the host's contestant map), so identity is now
# allocated per login and looked up by a client-sent key:
#
#   * `trackingSessionID` (startSession) / `streamline_session_id` (postTrackingInfo),
#   * the `hns_session` cookie the mock issues, or
#   * `/users/<player>/...`, whose path names the identity directly.
#
# Source IP is never used. `GUL_IID`/`GUL_NAME` pin one identity for single-client tests.
# ---------------------------------------------------------------------------
_CTX_DROPPED = frozenset(
    f.strip() for f in os.environ.get("GUL_CTX_DROP", "").split(",") if f.strip())

_NAME_PREFIX = os.environ.get("GUL_NAME_PREFIX", "Player")
_IID_OVERRIDE = os.environ.get("GUL_IID") or ""
_NAME_OVERRIDE = os.environ.get("GUL_NAME") or ""
_JWT_OVERRIDE = os.environ.get("GUL_JWT") or ""

_identity_lock = threading.Lock()
_identities = {}          # lookup key -> identity
_identity_by_iid = {}     # playerIID -> identity
_identity_by_name = {}    # displayName -> identity
_identity_seq = 0
CURRENT_IDENTITY = None


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "", text.lower()) or "player"


def _identity_jwt(iid):
    if _JWT_OVERRIDE:
        return _JWT_OVERRIDE
    payload = json.dumps({"user": iid}, separators=(",", ":")).encode("utf-8")
    return "x." + base64.urlsafe_b64encode(payload).decode() + ".z"


def _new_identity_locked(key):
    global _identity_seq
    _identity_seq += 1
    number = _identity_seq
    iid = _slug(_NAME_PREFIX) + str(number)
    ident = {
        "key": key,
        "iid": iid,
        "name": "%s%d" % (_NAME_PREFIX, number),
        "accountID": "a%d" % number,
        "jwt": _identity_jwt(iid),
        "cookie": "sess-" + hashlib.sha1(
            ("%s:%d" % (key, number)).encode()).hexdigest()[:24],
    }
    _identity_by_iid.setdefault(iid, ident)
    _identity_by_name.setdefault(ident["name"], ident)
    # Bind the cookie we will issue for this identity, so later keyless legs resolve it even
    # though the login request itself carried no cookie.
    _identities["cookie:" + ident["cookie"]] = ident
    return ident


def _identity_for_key(key):
    with _identity_lock:
        ident = _identities.get(key)
        if ident is None:
            ident = _new_identity_locked(key)
            _identities[key] = ident
        return ident


def _override_identity():
    iid = _IID_OVERRIDE or "devuser"
    name = _NAME_OVERRIDE or "DevUser"
    return {"key": "override", "iid": iid, "name": name, "accountID": "a1",
            "jwt": _identity_jwt(iid), "cookie": "dev1"}


_DEFAULT_IDENTITY = _override_identity() if (_IID_OVERRIDE or _NAME_OVERRIDE) else {
    "key": "default", "iid": "devuser", "name": "DevUser", "accountID": "a1",
    "jwt": _identity_jwt("devuser"), "cookie": "dev1"}


def current_identity():
    if CURRENT_IDENTITY is not None:
        return CURRENT_IDENTITY
    if _IID_OVERRIDE or _NAME_OVERRIDE:
        return _override_identity()
    return _DEFAULT_IDENTITY


def _iid():
    return current_identity()["iid"]


def _name():
    return current_identity()["name"]


def _account_id():
    return current_identity()["accountID"]


def _jwt():
    return current_identity()["jwt"]


def _account_record(ident, provider="twitch"):
    return {"provider": provider, "id": ident["iid"], "providerString": "twitch",
            "accountID": ident["accountID"], "displayName": ident["name"],
            "pgsToken": ident["jwt"]}


def session_context():
    """The shared session context with this request's identity substituted in."""
    ident = current_identity()
    ctx = dict(SESSION_CTX)
    fields = {
        "displayName": ident["name"], "playerIID": ident["iid"],
        "accountID": ident["accountID"], "pgsToken": ident["jwt"],
        "appAccountID": ident["accountID"], "pgsPlayerID": ident["iid"],
        "pgsPlayerToken": ident["jwt"],
    }
    for name, value in fields.items():
        if name in ctx and name not in _CTX_DROPPED:
            ctx[name] = value
    provider = ctx.get("provider", "twitch")
    account = _account_record(ident, provider)
    if "accounts" in ctx and "accounts" not in _CTX_DROPPED:
        ctx["accounts"] = [account]
    if "m_accounts" in ctx and "m_accounts" not in _CTX_DROPPED:
        ctx["m_accounts"] = [dict(account)]
    if os.environ.get("GUL_CTX_ECHO_SESSION"):
        sid = client_session_id()
        if sid:
            for name in ("sessionID", "m_sessionID", "trackingID", "m_trackingID",
                         "tracking_id", "session_id"):
                if name in ctx:
                    ctx[name] = sid
    return ctx


def handshake_context():
    """HANDSHAKE, with this request's identity when GUL_CTX_INTO_CONFIG is in play."""
    if not os.environ.get("GUL_CTX_INTO_CONFIG") or not _RAW_REQ.get("identity_explicit"):
        return HANDSHAKE
    merged = dict(HANDSHAKE)
    dynamic = session_context()
    for name in ("displayName", "playerIID", "accountID", "pgsToken", "accounts",
                 "m_accounts", "appAccountID", "pgsPlayerID", "pgsPlayerToken"):
        if name in dynamic:
            merged[name] = dynamic[name]
    return merged


def pgs_party():
    ident = current_identity()
    party = {"id": "p1", "owner": ident["iid"], "partyMax": 4, "users": [],
             "activePartyID": "", "invite_only": False}
    if _PGS_ACCOUNTS:
        account = _account_record(ident)
        party["accounts"] = [account]
        party["m_accounts"] = [dict(account)]
        party["users"] = [{"id": ident["iid"], "playerIID": ident["iid"],
                           "accountID": ident["accountID"],
                           "displayName": ident["name"], "provider": "twitch"}]
    return party


def _cookie_token():
    for part in _RAW_REQ.get("cookie", "").split(";"):
        key, sep, value = part.strip().partition("=")
        if sep and key == "hns_session":
            return value
    return ""


def _session_token():
    try:
        body = urllib.parse.unquote_plus(_RAW_REQ.get("body", ""))
    except Exception:
        return ""
    for field in ("trackingSessionID", "streamline_session_id"):
        # The wire form is `y<length>:<value>`; honour the declared length so a following
        # serialization byte cannot become part of the key.
        match = re.search(field + r"y(\d+):", body)
        if not match:
            continue
        length = int(match.group(1))
        token = body[match.end():match.end() + length]
        if re.fullmatch(r"[A-Za-z0-9_-]+", token):
            return token
    return ""


def _path_identity(path):
    match = re.search(r"/users/([^/]+)/updates(?:/|$)", path)
    if not match:
        return None
    wanted = urllib.parse.unquote(match.group(1))
    with _identity_lock:
        return _identity_by_iid.get(wanted) or _identity_by_name.get(wanted)


def _select_identity(path, op):
    """Pick this request's identity from a client-sent key; never from the source IP."""
    global CURRENT_IDENTITY
    CURRENT_IDENTITY = None
    _RAW_REQ["send_cookie"] = False
    _RAW_REQ["identity_explicit"] = False
    if _IID_OVERRIDE or _NAME_OVERRIDE:
        CURRENT_IDENTITY = _override_identity()
        _RAW_REQ["send_cookie"] = True
        _RAW_REQ["identity_explicit"] = True
        return CURRENT_IDENTITY

    cookie = _cookie_token()
    token = _session_token()
    ident = None
    if token:
        ident = _identity_for_key("session:" + token)
        if cookie:
            with _identity_lock:
                _identities["cookie:" + cookie] = ident
    elif cookie:
        ident = _identity_for_key("cookie:" + cookie)
    if ident is not None:
        CURRENT_IDENTITY = ident
        _RAW_REQ["send_cookie"] = True
        _RAW_REQ["identity_explicit"] = True
        return ident

    if op in ("startSession", "startSessionForFakePlayerID"):
        # A login with no tracking id still gets a fresh identity; the cookie issued on
        # this reply is what carries it to the next request.
        ident = _identity_for_key("login:%d" % time.time_ns())
        CURRENT_IDENTITY = ident
        _RAW_REQ["send_cookie"] = True
        _RAW_REQ["identity_explicit"] = True
        return ident

    ident = _path_identity(path)
    if ident is None:
        with _identity_lock:
            if len(_identity_by_iid) == 1:
                ident = next(iter(_identity_by_iid.values()))
    if ident is not None:
        CURRENT_IDENTITY = ident
    return ident


if os.environ.get("GUL_FAKE_PLAYER"):
    HANDSHAKE["fakePlayerID"] = os.environ["GUL_FAKE_PLAYER"]
    HANDSHAKE["pgsDevToken"] = os.environ.get("GUL_JWT", "x.eyJ1c2VyIjoiZGV2dXNlciJ9.z")

def _track_reply():
    """postTrackingInfo reply shapes. R785: the client's response layer parses the
    remoting body and needs the stream to satisfy the same grammar as doHandshake's.

    Live evidence (cf9.dmp): a SINGLE value ('oY6:statusY2:okg', 16 chars) made the
    client read one more value past the end → 'Invalid char \\0 at position 16' from
    FUN_141039c50 → Fatal error. Both later shapes below parse cleanly but the run then
    dies earlier (before CheckingAuth); see MODLOG R785.

      result   (default) [null echo][model.Result.Success(DSUR)] — the same envelope
               every other op serves and the one doHandshake demonstrably delivers with.
      object             [null echo][{"status":"ok"}]
      objtrail           [{"status":"ok"}][null]
      single             [{"status":"ok"}]  (the known-fatal one-value form)
    """
    # One-shot entity re-push after login. The client applies cosmetics once, from
    # startAppServerSessionForDRM, before the HOME pawn exists; the apply is silently
    # skipped when GetPawn() is null and never retried. A branch-B datastore update on a
    # post-LoggedIn tracking reply reaches onPlayerDataReceivedFromAppServer again, now
    # with a live pawn, and applies the same player entity.
    global _TRACK_PLAYER_PUSHED
    if os.environ.get("GUL_TRACK_PUSH_PLAYER") == "1" and not _TRACK_PLAYER_PUSHED:
        try:
            body = urllib.parse.unquote_plus(_RAW_REQ.get("body", ""))
        except Exception:
            body = ""
        if "LoggedIn" in body:
            _TRACK_PLAYER_PUSHED = True
            return _dsur(None)
    style = os.environ.get("GUL_TRACK_STYLE", "result")
    if style == "object":
        return haxe_ser.serialize(None) + haxe_ser.serialize({"status": "ok"})
    if style == "objtrail":
        return haxe_ser.serialize({"status": "ok"}) + haxe_ser.serialize(None)
    if style == "single":
        return haxe_ser.serialize({"status": "ok"})
    # The proven envelope: the exact bytes doHandshake uses — [null][Result.Success(<V-form
    # DSUR instance>)]. Every reply the client is known to handle is [<aux>][model.Result]:
    # the two other envelopes below put a plain object where a Result enum is expected
    # (that is what the R785 Fix-A/Fix-B runs died on: AV, then a secondary WIL
    # ResultException from C:\Windows\System32\fcon.dll on the crash-reporting path).
    if os.environ.get("GUL_TRACK_HX"):
        # postTrackingInfo is the one op the client calls over and over *before* the player lookup, so it is
        # the only "early and repeated" place branch-B elements can ride. Its reply is normally the same
        # branch-A envelope as doHandshake; this answers it through the op path instead, where the branch
        # choice (GUL_DSUR_B_OPS) and the element knobs apply. CURRENT_OP is already "postTrackingInfo".
        return _dsur(session_context())
    if os.environ.get("GUL_TRACK_CTX"):
        # postTrackingInfo is the op the client calls most, and its reply is the one envelope proven to be
        # handled. Carrying the session context here tests whether this, rather than the op whose name
        # suggests it, is what seeds the session datastore.
        return haxe_ser.handshake_result_dsur(session_context())
    return haxe_ser.handshake_result_dsur(None)


# op -> reply value (serialized inside hxr). None = serialized null (void).
    # op -> callable returning a SERIALIZED fragment (after 'hxr'), or a plain value.
    # Per DECOMPILE_NOTES: all remoting results must be model.Result enums; only
    # postTrackingInfo's value is provably unused (V10) so it stays a plain object.
# The op being served, set for the duration of each request. Lets the branch choice depend on which op
# the reply belongs to, which matters because branch A delivers the session context (Success(returnVal))
# while branch B drops it and delivers the inflated elements instead: the client may need the context on
# the op that seeds its session datastore and the elements on the op that loads the player.
CURRENT_OP = ""


def _branch_b_for_current_op():
    ops = os.environ.get("GUL_DSUR_B_OPS")
    if ops is not None:
        wanted = [o.strip() for o in ops.split(",") if o.strip()]
        return CURRENT_OP in wanted
    return bool(os.environ.get("GUL_DSUR_B"))


# Auxiliary ops the client calls before the session starts (checkVersion, getConfig, getOpenServer,
# getServerIp). The mock answers them with null; GUL_OP_CTX_OPS lists ops that should answer with the
# session context instead, since the context that seeds the datastore has to arrive on one of them.
OP_CTX_OPS = lambda: [o.strip() for o in os.environ.get("GUL_OP_CTX_OPS", "").split(",") if o.strip()]


def _dsur(payload):
    """Reply for an appserver op: DSUR branch A normally, branch B when GUL_DSUR_B is set.

    R792: branch A (empty updatedEntities) delivers Success(returnVal) unchanged and never touches
    the client's SessionDataStore, so 'accounts not in the datastore' kills the session start.
    Branch B hands entityTypeInfo to the client's inflater as the pushed entity updates. The
    shapes are env-driven so they can be iterated without editing code:
      GUL_DSUR_B=1
      GUL_DSUR_ENTITIES   JSON, what updatedEntities holds (only its LENGTH selects the branch)
      GUL_DSUR_BLOB_JSON  JSON, serialized into the entityTypeInfo string (the inflated payload)
    """
    direct_ops = os.environ.get("GUL_DSUR_DIRECT_OPS")
    if os.environ.get("GUL_DSUR_DIRECT") or (
            direct_ops is not None and CURRENT_OP in [o.strip() for o in direct_ops.split(",") if o.strip()]):
        # The payload itself as Success(value): what the local-player code reads its named fields off,
        # rather than a DSUR whose returnVal holds them. GUL_DSUR_DIRECT_VALUE=null sends a plain
        # Success(null), which is the differential test of whether the reply is processed at all before
        # the gate: if the gate is unchanged, nothing this op returns can matter.
        if os.environ.get("GUL_DSUR_DIRECT_VALUE") == "null":
            return haxe_ser.result_success(None)
        if os.environ.get("GUL_DSUR_DIRECT_VALUE") == "array":
            return haxe_ser.result_success([1, 2])
        return haxe_ser.result_success(payload)
    if _branch_b_for_current_op():
        ent_strs = os.environ.get("GUL_DSUR_ENTITIES_STR")
        raw = os.environ.get("GUL_DSUR_BLOB_RAW")
        im = os.environ.get("GUL_DSUR_BLOB_INTMAP")
        extra = ()
        if os.environ.get("GUL_DSUR_HX"):
            # Spec-aware branch B (DECOMPILE_NOTES R-spec): entityTypeInfo is the external type
            # cache stream and updatedEntities is an Array<String> of separately serialized objects,
            # each read with {typeInflater: cache, skipHeader: true}. Built by tools/hxinflate.py.
            # GUL_DSUR_HX_CLASSES: JSON list of {"class","custom","fields","body"[,"base","version"]}
            # one declaration + one serialized instance per entry, in order (cache indices 0..n-1).
            import hxinflate
            if os.environ.get("GUL_DSUR_HX_CONTAINER"):
                # The entity-holding object: a ContainerComponent whose blob id (per R823) is
                # "container_<container_>". It carries its own type cache for inflating what it
                # holds, so the shape is: name, the loaded entities keyed by iid, that cache text.
                ent4 = hxinflate.Decl("model.entity.ModelEntity", custom=True)
                cont = hxinflate.Decl("model.component.container.ContainerComponent", custom=False,
                                      fields=["container_", "m_loadedEntities", "m_numLoadedEntities",
                                              "m_inflaterTypeCache"])
                ent_body = [_iid(), "player", {}, None, None, None, ""]
                ent_inst = hxinflate.Instance(values=ent_body, decl=ent4)
                blob = hxinflate.type_cache([ent4, cont], purpose=None)
                cont_inst = hxinflate.Instance(
                    values=["gul", {_iid(): ent_inst}, 1, blob],
                    decl=cont)
                ents = [hxinflate.object_stream(cont_inst)]
                return haxe_ser.dsur_branch_b(payload, ents, blob, ())
            if os.environ.get("GUL_DSUR_HX_ACCOUNTS"):
                # The datastore entry the login flow asks for is named "accounts", and the session
                # config carries accounts as an Array of {id, accountID, displayName, pgsToken}
                # records. Push exactly that as its own element, alongside the safe player entity, so
                # the value type is the one the config already uses.
                ent3 = hxinflate.Decl("model.entity.ModelEntity", custom=True)
                body3 = [_iid(), "player", {}, None, None, None, ""]
                acct = {"id": _iid(),
                        "accountID": _account_id(),
                        "displayName": _name(),
                        "pgsToken": _jwt()}
                blob = hxinflate.type_cache([ent3], purpose=None)
                ents = [hxinflate.object_stream(hxinflate.Instance(values=body3, decl=ent3)),
                        hxinflate.object_stream([acct])]
                return haxe_ser.dsur_branch_b(payload, ents, blob, ())
            if os.environ.get("GUL_DSUR_HX_TEMPLATE"):
                # Push a ModelEntityTemplate blob alongside the entity, so templateID resolves.
                # Shape per the game's own compiled data (TPLC blob in the pak):
                #   V model.tpl.ModelEntityTemplate :i-1:z:f: 4:
                #     componentDefinitions componentHash params templateID :
                # Non-custom, and the componentDefinitions entries are ComponentDefinition objects
                # with fields autoConstruct, name, Data.
                ent2 = hxinflate.Decl("model.entity.ModelEntity", custom=True)
                tpl = hxinflate.Decl("model.tpl.ModelEntityTemplate", custom=False,
                                     fields=["componentDefinitions", "componentHash", "params",
                                             "templateID"])
                cdef = hxinflate.Decl(
                    "model.tpl.ComponentDefinition", custom=False,
                    fields=json.loads(os.environ.get(
                        "GUL_DSUR_HX_CDEF_FIELDS",
                        '["autoConstruct","name","templateData"]')))
                comp_defs = {}
                if not os.environ.get("GUL_DSUR_HX_EMPTY_TEMPLATE"):
                    # Field-name candidates come from the class's hxcpp name table; the values here
                    # are also overridable so a variant is an env change rather than an edit.
                    # templateData carries a ComponentPropertyDefinition instance, not a map: the
                    # game's own blob shows a nested V instance there, declared custom = t (so it
                    # serializes itself and the declaration carries no field list).
                    prop_custom = os.environ.get("GUL_DSUR_HX_PROP_CUSTOM", "1") != "0"
                    prop = hxinflate.Decl(
                        "model.tpl.ComponentPropertyDefinition", custom=prop_custom,
                        fields=[] if prop_custom else ["property"])
                    prop_values = json.loads(os.environ.get("GUL_DSUR_HX_PROP_VALUES",
                                                            "[null,null]"))
                    if os.environ.get("GUL_DSUR_HX_PROP_ENTITY"):
                        # The container holds entities (m_loadedEntities), so the entity belongs inside
                        # the component's property rather than merely beside it: value 1 of the nested
                        # definition becomes a map from iid to the entity instance itself. The body
                        # stays at two values, which is the arity the client demands (R841).
                        ent_inst2 = hxinflate.Instance(
                            values=[_iid(), tid,
                                    {}, None, None, None, ""], decl=ent2)
                        second = os.environ.get("GUL_DSUR_HX_PROP_SECOND")
                        prop_values = [{_iid(): ent_inst2},
                                       json.loads(second) if second else None]
                    prop_inst = hxinflate.Instance(values=prop_values, decl=prop)
                    # FUN_1410583a0 iterates cdef.templateData as an Array and hands each
                    # element to FUN_141057200 as a String. An object instance there is read as
                    # an array header and crashes; default to the safe empty array.
                    if os.environ.get("GUL_DSUR_HX_PROP_ARRAY", "1") != "0":
                        template_value = json.loads(os.environ.get(
                            "GUL_DSUR_HX_PROP_ARRAY_VALUE", "[]"))
                    else:
                        template_value = prop_inst
                    # A list of component class names, so a template's component set can be bisected
                    # one entry at a time; each gets the same definition shape.
                    comp_names = json.loads(os.environ.get(
                        "GUL_DSUR_HX_COMPONENTS",
                        json.dumps([os.environ.get("GUL_DSUR_HX_COMPONENT",
                                                   "component.player.AccountComponent")])))
                    # GUL_DSUR_HX_BAGS: {componentClass: {field: value}} - each definition's
                    # templateData becomes that component's own field bag, which is what
                    # getComponentData hands to the component's construction (R868). Falls back to the
                    # single shared property body when unset.
                    bags = json.loads(os.environ.get("GUL_DSUR_HX_BAGS", "{}"))
                    for cname in comp_names:
                        if bags.get(cname):
                            # The game's own TPLC declaration (and FUN_14104f510's boot order)
                            # serializes exactly autoConstruct, name, templateData; m_class is not
                            # on the wire. Values before R925 were positionally shifted, so the
                            # component received the Bool as its template data.
                            cdef_bag = hxinflate.Instance(
                                values=[True, cname, bags[cname]], decl=cdef)
                            comp_defs[cname] = cdef_bag
                            continue
                        cdef_inst = hxinflate.Instance(
                            values=json.loads(os.environ.get("GUL_DSUR_HX_CDEF_VALUES",
                                '[true,"component.player.AccountComponent",null]')),
                            decl=cdef)
                        # substitute the nested property instance and this component's own class name
                        no_prop = os.environ.get("GUL_DSUR_HX_NO_PROP") == "1"
                        cdef_inst.values = [
                            None if (v is None and no_prop)
                            else (template_value if v is None
                                  else (cname if v == "component.player.AccountComponent" else v))
                            for v in cdef_inst.values]
                        comp_defs[cname] = cdef_inst
                tid = os.environ.get("GUL_IID_TEMPLATE", "player")
                tpl_inst = hxinflate.Instance(
                    values=[comp_defs, "", {}, os.environ.get("GUL_IID_TEMPLATE_BLOB", tid)], decl=tpl)
                body2 = [_iid(), tid, {}, None, None, None, ""]
                ent_fields = os.environ.get("GUL_DSUR_HX_ENTITY_FIELDS")
                if ent_fields:
                    # The entity's StringMap (body value 3) as *named fields*. The session-context
                    # reader looks its fields up by name (accounts, pgsToken, displayName, playerIID,
                    # accountID, provider, session), and a datastore blob is addressed by the entity's
                    # own name, so an entity named "accounts" carrying these fields is the shape that
                    # would satisfy that reader.
                    body2[2] = json.loads(ent_fields)
                extra_decls = []
                if os.environ.get("GUL_DSUR_HX_MAP_COMPONENT") == "1":
                    # The entity's StringMap (body value 3) may be the component carrier: a map from
                    # component class to an inline instance, instead of (or as well as) the inline
                    # convention. Only tested now because the rest of the payload is accepted (R855).
                    mcls = hxinflate.Decl("model.component.player.AccountComponent", custom=False,
                                          fields=["accountProvider", "playerID", "isSecondaryAccount",
                                                  "linkedAccount", "overriddenPlayerIID", "renamedTo",
                                                  "pgsToken", "icons"])
                    minst = hxinflate.Instance(
                        values=[None, _iid(), False, None,
                                _iid(), None,
                                _jwt(), None],
                        decl=mcls)
                    body2[2] = {"component.player.AccountComponent": minst}
                    extra_decls.append(mcls)
                if os.environ.get("GUL_DSUR_HX_TEMPLATE_INLINE") == "1":
                    # A templated entity may need its components' *instances* as well as the template
                    # that declares them. Pair the template's component with an inline instance of the
                    # same class, using a class whose body is known (AccountComponent, non-custom).
                    icls = hxinflate.Decl("model.component.player.AccountComponent", custom=False,
                                          fields=["accountProvider", "playerID", "isSecondaryAccount",
                                                  "linkedAccount", "overriddenPlayerIID", "renamedTo",
                                                  "pgsToken", "icons"])
                    iinst = hxinflate.Instance(
                        values=[None, _iid(), False, None,
                                _iid(), None,
                                _jwt(), None],
                        decl=icls)
                    extra_decls.append(icls)
                    body2 = [_iid(), tid, {}, None, None,
                             json.loads(os.environ.get("GUL_DSUR_HX_N6", '["x"]')), iinst]
                # ModelEntity.hxUnserialize reads exactly seven top-level values; the seventh is the
                # entity's componentHash. When it equals the template's componentHash, awaken returns
                # Success before creating any components. This is the minimal devuser carrier.
                component_hash = os.environ.get("GUL_DSUR_HX_COMPONENT_HASH")
                tail_default = "1"
                if os.environ.get("GUL_DSUR_HX_TEMPLATE_INLINE") != "1" and component_hash is not None:
                    body2[6] = component_hash
                    tail_default = "0"
                body2 += [""] * int(os.environ.get("GUL_DSUR_HX_TAIL", tail_default))
                blob = hxinflate.type_cache([ent2, tpl, cdef] + extra_decls,
                                           purpose=os.environ.get("GUL_DSUR_HX_PURPOSE", "game"))
                # branch-B updatedEntities are inflated one by one and dyncast to model.ModelEntity
                # (FUN_1411f73a0). A ModelEntityTemplate there cannot cast and reaches awaken(null),
                # which is the C++ EH crash. Templates must not be sent as elements; the client
                # resolves templates from its own template store, so an entity templateID must name
                # one the client already knows (for example PGSFakePlayer).
                tids = json.loads(os.environ.get("GUL_DSUR_HX_TEMPLATE_IDS",
                                                 json.dumps([os.environ.get("GUL_IID_TEMPLATE_BLOB", tid)])))
                # Applied last: the INLINE path rebuilds body2, so the entity's named fields must be
                # set after every branch that touches it.
                ent_fields = os.environ.get("GUL_DSUR_HX_ENTITY_FIELDS")
                if ent_fields:
                    body2[2] = json.loads(ent_fields)
                ents = []
                element_purpose = os.environ.get("GUL_DSUR_HX_PURPOSE", "game")
                if os.environ.get("GUL_DSUR_HX_TEMPLATE_ELEMENTS") == "1":
                    for one in tids:
                        ents.append(hxinflate.object_stream(
                            hxinflate.Instance(values=[comp_defs, "", {}, one], decl=tpl),
                            purpose=element_purpose))
                ents.append(hxinflate.object_stream(
                    hxinflate.Instance(values=body2, decl=ent2),
                    purpose=element_purpose))
                # Additional entities can ride the same reply. Each one must still be a
                # ModelEntity and carries its own templateID/componentHash; the account entity
                # is the next datastore key the login asks for.
                for spec in json.loads(os.environ.get("GUL_DSUR_HX_EXTRA_ENTITIES", "[]")):
                    body_extra = [spec["iid"], spec["template"], {}, None, None, None,
                                  spec["hash"]]
                    ents.append(hxinflate.object_stream(
                        hxinflate.Instance(values=body_extra, decl=ent2),
                        purpose=element_purpose))
                # A comma list of extra entity names, each appended as a full entity (built by the same
                # path, so the shape parses). One probe can then satisfy every candidate blob id at once,
                # and the gate's message - which names the *next* missing id - reports progress.
                for one_iid in [x.strip() for x in os.environ.get(
                        "GUL_DSUR_HX_EXTRA_IIDS", "").split(",") if x.strip()]:
                    body4 = list(body2)
                    body4[0] = one_iid
                    exf = os.environ.get("GUL_DSUR_HX_EXTRA_FIELDS")
                    if exf:
                        body4[2] = json.loads(exf)
                    ents.append(hxinflate.object_stream(
                        hxinflate.Instance(values=body4, decl=ent2),
                        purpose=element_purpose))
                extra_iid = os.environ.get("GUL_DSUR_HX_EXTRA_IID")
                if extra_iid:
                    # A second entity, built by the very same path as the first (so its shape parses),
                    # named by the knob and optionally carrying its own named fields. This is how an
                    # entity named "accounts" can exist beside the player's own without hand-writing a
                    # fragment whose arity is guesswork.
                    body3 = list(body2)
                    body3[0] = extra_iid
                    ex_fields = os.environ.get("GUL_DSUR_HX_EXTRA_FIELDS")
                    if ex_fields:
                        body3[2] = json.loads(ex_fields)
                    ents.append(hxinflate.object_stream(
                        hxinflate.Instance(values=body3, decl=ent2),
                        purpose=element_purpose))
                extra = os.environ.get("GUL_DSUR_HX_EXTRA_ELEMENT")
                if extra:
                    # An extra branch-B element, spliced verbatim: used to test whether a second
                    # element named "accounts" writes the datastore entry the login gate asks for.
                    ents.append(hxinflate.RawFragment(extra))
                return haxe_ser.dsur_branch_b(payload, ents, blob, ())
            if os.environ.get("GUL_DSUR_HX_INLINE_COMPONENT"):
                # Components are NOT acceptable as standalone elements (they crash the push path),
                # so an account rides inside the entity's own stream: ModelEntity body value #6 is a
                # container whose length gates an inline component read (docs/model-entity-wire.md
                # section 1), the instances follow it, and the trailing String stays last.
                ent = hxinflate.Decl("model.entity.ModelEntity", custom=True)
                comp = hxinflate.Decl(
                    "model.component.player.AccountComponent", custom=False,
                    fields=["accountProvider", "playerID", "isSecondaryAccount", "linkedAccount",
                            "overriddenPlayerIID", "renamedTo", "pgsToken", "icons"])
                comp_inst = hxinflate.Instance(
                    values=[None, _iid(), False, None,
                            _iid(), None,
                            _jwt(), None],
                    decl=comp)
                body = [_iid(),
                        os.environ.get("GUL_IID_TEMPLATE", "player"), {}, None, None,
                        json.loads(os.environ.get("GUL_DSUR_HX_N6", '["x"]')), comp_inst]
                body += [""] * int(os.environ.get("GUL_DSUR_HX_TAIL", "1"))
                blob = hxinflate.type_cache([ent, comp], purpose="game")
                ents = [hxinflate.object_stream(hxinflate.Instance(values=body, decl=ent),
                                                purpose="game")]
                return haxe_ser.dsur_branch_b(payload, ents, blob, ())
            specs_raw = os.environ.get("GUL_DSUR_HX_CLASSES")
            if specs_raw:
                specs = json.loads(specs_raw)
            else:
                specs = [{"class": os.environ.get("GUL_DSUR_HX_CLASS", "model.entity.ModelEntity"),
                          "custom": True,
                          "body": json.loads(os.environ.get("GUL_DSUR_HX_BODY", "[]"))}]
            decls, ents = [], []
            for sp in specs:
                d = hxinflate.Decl(sp["class"], base=sp.get("base", -1), version=sp.get("version", 0),
                                   custom=sp.get("custom", True), fields=sp.get("fields", ()))
                decls.append(d)
                ents.append(hxinflate.object_stream(
                    hxinflate.Instance(values=sp.get("body", []), decl=d)))
            blob = hxinflate.type_cache(decls, purpose=None)
        else:
            ents_raw = os.environ.get("GUL_DSUR_ENTITIES_RAW")
            ents = haxe_ser.RawFragment(ents_raw) if ents_raw is not None else json.loads(os.environ.get("GUL_DSUR_ENTITIES", "[{}]"))
            if raw is not None:
                blob = raw
            elif im is not None:
                # A v3 type-info stream: header, purpose, then the type info.
                entries = "".join("i" + str(k) + haxe_ser.serialize(v) for k, v in json.loads(im).items())
                blob = "ZVER3" + "q" + entries + "h"
            else:
                blob = haxe_ser.serialize(json.loads(os.environ.get("GUL_DSUR_BLOB_JSON", "[]")))
        if ent_strs is not None:
            ents = json.loads(ent_strs)          # Array<String>, each an object stream
        if os.environ.get("GUL_DSUR_ENTITY"):
            # Legacy carrier experiment (superseded): declare a class and put the stream in a slot.
            ent_cls = haxe_ser.HxClass("model.entity.ModelEntity",
                                       ["m_class", "m_name", "f2", "f3", "f4", "f5", "f6", "f7",
                                        "m_components"])
            ents = haxe_ser.HxInstance(ent_cls, [None, blob, 0, 0, 0, 0, 0, 0, []])
            extra = (ent_cls,)
        return haxe_ser.dsur_branch_b(payload, ents, blob, extra)
    return haxe_ser.handshake_result_dsur(payload)


def process_url_join_reply():
    """Reply for processUrl: Result.Success(UrlAction.UJoinGame({host,port,...})).

    The client travels to host:port on tag 0. The URL string itself is opaque; the
    server decides the destination. Options here are lab-controlled.
    """
    host = os.environ.get("GUL_GAME_HOST", "127.0.0.1")
    port = int(os.environ.get("GUL_GAME_PORT", "7777"))
    player_session = os.environ.get("GUL_JOIN_SESSION", "client-1")
    party = os.environ.get("GUL_JOIN_PARTY", "")
    game_session = os.environ.get("GUL_JOIN_GAMESESSION", "")
    s = haxe_ser.Serializer()
    kind = os.environ.get("GUL_URL_ACTION", "join")
    if kind == "nogame":
        return haxe_ser.handshake_result_dsur(
            haxe_ser.HxEnum("appserver.api.UrlAction", 1, []))
    if kind == "error":
        return ("n" + "w" + s.value("model.Result") + s.value("Error") + ":1"
                + s.value("join-failed"))
    if kind == "denied":
        return haxe_ser.handshake_result_dsur(
            haxe_ser.HxEnum("appserver.api.UrlAction", 3, []))
    # processUrl is a public-plane op: its Success payload must be a branch-A
    # DataStoreUpdateResponse (the consumer dyncasts to it before reading returnVal). A bare
    # Result.Success(UrlAction) fails that cast with a null dereference and the handler never runs.
    action = haxe_ser.HxEnum("appserver.api.UrlAction", 0, [{
        "host": host, "port": port, "party": party,
        "playerSession": player_session, "gameSession": game_session}])
    return haxe_ser.handshake_result_dsur(action)


OP_REPLIES = {
    # Round 5: every XIAppServerPublic RPC result is wrapped in DataStoreUpdateResponse
    # (branch A: empty updatedEntities → returnVal passthrough).
    "doHandshake": lambda: _dsur(handshake_context()),
    # The private appserver API (remoting.proxy.appserver.api.XIAppServerPrivate) declares a
    # fake-player path: startSessionForFakePlayerID, loadPlayerFromSession,
    # loadPlayerFromFakePlayerID and getAccountInfo. If the client selects that path (config
    # clientConfigManager.fakePlayerID), these are where the player and its account state belong, so
    # they share the same branch-B reply machinery as startSession.
    "startSessionForFakePlayerID": lambda: _dsur(handshake_context()),
    "loadPlayerFromFakePlayerID": lambda: _dsur(handshake_context()),
    "loadPlayerFromSession": lambda: _dsur(handshake_context()),
    # UI-free autojoin: -RunURL=<value> reaches processUrl; tag 0 (UJoinGame) makes the
    # client ClientTravel to the host below. See runtime/PROCESSURL_JOIN.md.
    "processUrl": process_url_join_reply,
    "getAccountInfo": lambda: _dsur(handshake_context()),
    # GUL_TRACKING=null → reply "nn" (void-style) for fan-out variants.
    # R785: the client's UE4Client_obj::handleResponse reads TWO values from every
    # remoting body (mirror of the request's own [null echo][payload] shape).
    # Live proof from cf9.dmp: this op's reply carried ONE value ('oY6:statusY2:okg',
    # 16 chars); the second read stepped past the buffer end → nextChar()==0 →
    # "Invalid char \0 at position 16" thrown from FUN_141039c50 → Fatal error.
    # Two values are required, and value 2 must be NON-NULL (Round 655: every silent
    # run had value2='n'), so send [null echo][the value].
    "postTrackingInfo": _track_reply,
    # Round 14 latch theory: the tracking response (arriving first) sets the done-latch on the
    # SHARED handleResponse context (param_1+0x18), silently swallowing the handshake response.
    # Delay it past the handshake's processing window.
    "checkVersion": lambda: _dsur(session_context() if "checkVersion" in OP_CTX_OPS() else None),
    "startSession": lambda: _dsur(session_context()),
    "clientHeartbeat": lambda: _dsur({"interval_sec": 60}),
    "getConfig": lambda: _dsur(session_context() if "getConfig" in OP_CTX_OPS() else None),
    "getOpenServer": lambda: haxe_ser.result_success(
        HxEnum("appserver.api.OpenServerResponse", 0, args=[{
            "host": os.environ.get("GUL_GAME_HOST", "127.0.0.1"),
            "port": int(os.environ.get("GUL_GAME_PORT", "7777")),
            "party": "",
            "playerSession": "",
            "gameSession": "",
        }])),
    # Dedicated-server registration / activation (private plane).
    "getCurrentIP": lambda: haxe_ser.result_success(
        os.environ.get("GUL_PUBLIC_IP", "127.0.0.1")),
    "getServerOptions": lambda: haxe_ser.result_success({
        "gameKind": HxEnum("appserver.api.GameKind", 2, []),  # KNoHost
        "partyID": "",
        "hostToken": "",
        "gsess": "",
        "urlVisibility": "public",
    }),
    "hostedServerHeartbeat": lambda: haxe_ser.result_success({}),
    "onEndMatch": lambda: haxe_ser.result_success({}),
    "giveEndOfMatchRewards": lambda: haxe_ser.result_success({}),
    "hostHasJoined": lambda: haxe_ser.result_success({}),
    "getServerIp": lambda: _dsur(session_context() if "getServerIp" in OP_CTX_OPS() else None),
}


# The client generates its own session id and sends it on startSession (`trackingSessionID`, a 32-hex
# string); the session context it then reads carries sessionID/trackingID, and the mock has been sending
# placeholders ("s1"/"t1") that the client rejects as enum names. GUL_CTX_ECHO_SESSION reflects the client's
# own id back in those fields, which is what a server would do.
_RAW_REQ = {"body": "", "cookie": "", "send_cookie": False, "identity_explicit": False}
_TRACK_PLAYER_PUSHED = False


def client_session_id():
    """The login's tracking id, from either body field the client uses for it."""
    return _session_token()


def slog(line):
    path = os.environ.get("GUL_LOG", LOG_PATH)
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    with open(path, "a") as f:
        f.write(line + "\n")


class Handler(BaseHTTPRequestHandler):
    # HTTP/1.0: one request per connection — the client pipelines tracking+handshake
    # on keep-alive sockets and concurrent writes INTERLEAVE responses (the "Invalid char
    # \0 at position 615" bug — 615 bytes of mixed streams before a NUL).
    protocol_version = "HTTP/1.0"  # the era when extraction demonstrably worked
    _lock = threading.Lock()

    def _reply(self, body: bytes, status=200, ctype="text/plain"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        if os.environ.get("GUL_NOCL", "0") == "0":
            self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")  # Round 19: force close-delimited body reads (GPTK truncation fix)
        # Round 17: the branch predicate = (DIL!=0 && kind==Int && 200<=code<=206).
        # The responseCode can arrive as a null Dynamic under wine (→ coerced 0 → error branch).
        # The redundant CGI-style Status header is the discriminator.
        if os.environ.get("GUL_STATUS", "0") != "0":
            self.send_header("Status", str(status))
        # Round 18: TWO Set-Cookie headers were the seam. The client's CookieClient
        # (sl.remoting.CookieClient, FUN_1408f8050) parses EVERY Set-Cookie via an attribute-
        # dispatch chain (expires/max-age/secure/httponly) BEFORE handleResponse runs. Our mock
        # sent `Path=/`+`HttpOnly` (no Expires/Max-Age) on two headers; the second header's
        # attribute parse throws a Haxe Dynamic that the frame above swallows → silent return,
        # so the debugger sees death between handleResponse entry and the body branch.
        # The remoting flow needs NO cookie — the earlier era (no Set-Cookie) proved the
        # extraction worked. Default to NO cookie; opt back in with GUL_COOKIES=1.
        #
        # When enabled, the cookie carries this request's identity. It is only issued on a
        # request that actually resolved an identity (a login, a tracking id, or a cookie), so
        # a pre-login handshake cannot hand one client another client's session.
        if os.environ.get("GUL_COOKIES", "0") != "0" and _RAW_REQ.get("send_cookie"):
            ident = current_identity()
            self.send_header("Set-Cookie",
                             "hns_session=%s; Path=/; HttpOnly" % ident["cookie"])
        self.end_headers()
        self.wfile.write(body)

    def _handle(self):
        with Handler._lock:
            self._handle_inner()

    def _handle_inner(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        # Log the *request* body as well as the reply: the client serialises its own objects on the way out
        # (its session context among them, on the tracking calls), so its encoding of a field is ground truth
        # for the types it expects - the mock's replies only show what it is willing to send.
        try:
            _RAW_REQ["body"] = raw.decode("latin1")
        except Exception:
            pass
        _RAW_REQ["cookie"] = self.headers.get("Cookie", "") or ""
        if raw and os.environ.get("GUL_LOG_REQ", "1") == "1":
            try:
                slog("    REQ  %s -> %s" % (self.path, raw.decode("latin1")[:800]))
            except Exception:
                pass
        ts = time.strftime("%H:%M:%S")
        slog(f"=== {ts} {self.command} {self.path}")
        for k, v in self.headers.items():
            slog(f"    H {k}: {v}")
        slog(f"    BODY({len(raw)}): {raw[:2000]!r}")

        parsed = urllib.parse.parse_qs(raw.decode("latin-1"), keep_blank_values=True)
        if "__x" in parsed:
            payload = parsed["__x"][0]
            try:
                path, params = haxe_ser.unserialize_two(payload)
                slog(f"    DECODE path={path!r}")
                slog(f"    DECODE params={json.dumps(params, default=str)[:1500]}")
            except Exception as e:  # keep serving even if decode fails
                slog(f"    DECODE FAILED: {e}")

        # route
        p = self.path.split("?")[0].rstrip("/")
        op = p.rsplit("/", 1)[-1] if p else ""
        globals()["CURRENT_OP"] = op
        ident = _select_identity(p, op)
        if op == "startSession" and ident is not None:
            slog("    IDENTITY %s (playerIID=%s accountID=%s cookie=%s)"
                 % (ident["name"], ident["iid"], ident["accountID"], ident["cookie"]))
        if op == "startSession":
            # A new login re-arms the one-shot post-login entity push.
            globals()["_TRACK_PLAYER_PUSHED"] = False
        if "/remoting" in p and "__x" in parsed:
            if op == "postTrackingInfo":
                style = os.environ.get("GUL_TRACK_STYLE", "ok")
                if style == "empty":
                    slog("    (tracking → empty body, error path)")
                    self._reply(b"")
                    return
                if style == "garbage":
                    slog("    (tracking → garbage body, error path)")
                    self._reply(b"NOTHXRATALL")
                    return
                if style == "404":
                    slog("    (tracking → 404, error path)")
                    self._reply(b"nope", status=404)
                    return
                d = float(os.environ.get("GUL_TRACK_DELAY", "10"))
                slog(f"    (tracking reply delayed {d}s — latch theory)")
                time.sleep(d)
            reply = OP_REPLIES.get(op, "UNKNOWN_OP")
            # GUL_HS_MODE=error — Round-20 discriminator: serve model.Result.Error so the
            # dispatcher's tag==0 branch logs "doClientHandshake error: <msg>" + setError.
            # Marker seen  => HTTP+parse+cast+invoke+dispatcher all work (stall is Success-path).
            # Marker absent => break is upstream of the dispatcher (body/parse/cast/invoke).
            if op == "doHandshake" and os.environ.get("GUL_HS_MODE") == "error":
                # Two-value envelope: [path-echo null, result] — a lone result makes the
                # client read value 2 past the buffer end ("Invalid char \0" throw).
                frag = "n" + haxe_ser.result_error("MARKER123")
            elif op == "doHandshake" and os.environ.get("GUL_HS_MODE") == "error3":
                # Round 639 model (fits all 5 runs): TWO values read; value1 = delivered result;
                # value2 must parse (pad with 'n'); the digit after ctor name = CTOR INDEX
                # (Error=0, Success=1) — ':1' made the client read Error as Success -> consumer
                # cast AV (silent Fatal in the error2 run).
                # Prediction: :493 logs "doClientHandshake error: MARKER123" VERBATIM.
                s = haxe_ser.Serializer()
                frag = ("w" + s.value("model.Result") + s.value("Error") + ":0"
                        + s.value("MARKER123") + "n")
            elif op == "doHandshake" and os.environ.get("GUL_HS_MODE") == "error4":
                # Round 655 discriminator: value1 = valid Error(1 arg), value2 = i5 (NOT 'n').
                # Every SILENT run had value2='n'; every logged run had value2 != 'n' or a
                # thrown value1. Isolates whether the mystery second-pass reader AVs on null.
                #   "MARKER123" logged  => value1 delivered fine; use a non-null trailing value.
                #   silent again        => value1 delivery itself AVs; value2-delivery rises.
                frag = haxe_ser.result_error("MARKER123") + "i5"
            elif op == "doHandshake" and os.environ.get("GUL_HS_MODE") == "success1":
                # Round 701 isolation probe: value1 = null echo, value2 = w Success + string
                # payload (no V-decl). error5 proved w-Error parses; if this AVs at the payload
                # cast (0x1411F781E) w-Success parses too -> the enum-format throw seen with the
                # i-1 decl body belongs to the decl/version boundary, not the enum.
                frag = haxe_ser.result_success("PROBE123")
            elif op == "doHandshake" and os.environ.get("GUL_HS_MODE") == "resultdsur":
                # Round 726: [n][Result.Success(<self-contained V-form DSUR instance>)] — the
                # V token is now a COMPLETE value ([decl][3 field values][g]) so nothing is eaten.
                # Honor per-op branch selection here too: the explicit doHandshake format used to
                # bypass _dsur(), so GUL_DSUR_B_OPS=doHandshake silently remained branch A. Route
                # through _dsur only when the caller selected this op; with no branch selection the
                # proven handshake envelope remains byte-for-byte unchanged.
                branch_ops = os.environ.get("GUL_DSUR_B_OPS", "")
                if "doHandshake" in [x.strip() for x in branch_ops.split(",") if x.strip()]:
                    frag = _dsur(handshake_context())
                else:
                    frag = haxe_ser.handshake_result_dsur(handshake_context())
            elif op == "doHandshake" and os.environ.get("GUL_HS_MODE") == "declpair":
                # Round 726b: [<V-form DSUR>][Result.Success(<T0 DSUR>)] (echo = the instance).
                frag = haxe_ser.handshake_decl_plus_t0(handshake_context())
            elif op == "doHandshake" and os.environ.get("GUL_HS_MODE") == "declinst":
                # Round 723: decl + null + Success(string) + tokenless DSUR instance (fields+g).
                frag = haxe_ser.handshake_decl_enum_inst(handshake_context())
            elif op == "doHandshake" and os.environ.get("GUL_HS_MODE") == "decltrail":
                # Round 716: decl + null slot + Success(string) + trailing n + @ position marker.
                frag = haxe_ser.handshake_decln_trail("PROBE123")
            elif op == "doHandshake" and os.environ.get("GUL_HS_MODE") == "declnstr":
                # Round 714: decl + explicit null echo slot + Success(string).
                frag = haxe_ser.handshake_decln_str("PROBE123")
            elif op == "doHandshake" and os.environ.get("GUL_HS_MODE") == "declstr":
                # Round 708 boundary discriminator: identical i-1 decl + Success(string).
                frag = haxe_ser.handshake_decl_str("PROBE123")
            elif op == "doHandshake" and os.environ.get("GUL_HS_MODE") == "error5":
                # Round 669 adjudicator: value1 = Error(MARKER_A), value2 = Error(MARKER_B).
                # Both slots non-null + castable to model.Result. Outcomes:
                #   "MARKER_A" -> value1 is delivered (error2/4's AV = value2's nullness handled
                #                 before delivery); "MARKER_B" -> value2 is delivered;
                #   silent     -> wire Results cast to NULL (consumer entry AVs on any wire value).
                frag = haxe_ser.result_error("MARKER_A") + haxe_ser.result_error("MARKER_B")
            elif op == "doHandshake" and os.environ.get("GUL_HS_MODE") == "error2":
                # Round 635 model: value1 = DELIVERED result, value2 = parsed padding.
                # Prediction: dispatcher logs "doClientHandshake error: MARKER123" verbatim.
                frag = haxe_ser.result_error("MARKER123") + "n"
            elif op == "doHandshake" and os.environ.get("GUL_HS_MODE") == "bareerror":
                # Round 24: bare Error (NO V-decl, NO n-echo) under the FIXED latch.
                # If this LOGS at :493 but verror (V-decl-wrapped Error) is silent, the V-decl
                # envelope is confirmed as the sole thing that makes the client drop the result.
                frag = haxe_ser.result_error("BAREMARKER")
            elif op == "doHandshake" and os.environ.get("GUL_HS_MODE") == "verror":
                # Round 610 discriminator: SAME V-decl envelope as handshake_response, but the
                # enum is Result.Error. Round-20 proved a *bare* Error reaches :493 + logs.
                #   Marker logged  => the V-decl envelope delivers a Result to :493 fine, so the
                #                     Success-path silence is DOWNSTREAM (processHandshakeResult).
                #   Marker absent  => the V-decl envelope itself drops the result before :493.
                s = haxe_ser.Serializer()
                decl = ("V" + s.value("appserver.remoting.DataStoreUpdateResponse")
                        + ":i0:i0:i0:3:"
                        + s.value("updatedEntities") + s.value("entityTypeInfo") + s.value("returnVal")
                        + ":")
                frag = decl + "w" + s.value("model.Result") + s.value("Error") + ":0" + s.value("VDECLMARKER")
            elif callable(reply):
                frag = reply()
            elif isinstance(reply, str) and reply.startswith("EXC:"):
                frag = haxe_ser.serialize_exception(reply[4:])
            elif reply == "UNKNOWN_OP":
                # R791: was serialize_exception("unknown op X"). An exception makes the client
                # error out and stop; a Success with an empty DSUR (the same envelope
                # postTrackingInfo/doHandshake demonstrably deliver) lets it continue, and its own
                # log then names whatever data it actually needed. The op is still logged loudly.
                slog(f"    (UNKNOWN OP {op!r} -> Success/empty DSUR so the client can proceed)")
                frag = haxe_ser.handshake_result_dsur(None)
            else:
                frag = haxe_ser.serialize(reply)
            diag = os.environ.get("GUL_DIAG", "")
            if diag and op == "doHandshake":
                frag = diag  # raw fragment override for runtime probes (e.g. "i5", "n")
            # GUL_COMPRESS: none (correct per DECOMPILE_NOTES R2 — body is PASS-THROUGH),
            # zlib/raw kept only as historical probes (both misread as exception/invalid char).
            mode = os.environ.get("GUL_COMPRESS", "none")
            fb = frag.encode()
            if mode == "zlib":
                body = b"hxr" + zlib.compress(fb)
            elif mode == "raw":
                c = zlib.compressobj(wbits=-15)
                body = b"hxr" + c.compress(fb) + c.flush()
            elif mode == "raw4":
                # skipHeader consumes a fixed 4-byte prefix, then inflates the rest raw
                c = zlib.compressobj(wbits=-15)
                body = b"hxr" + b"\x00\x00\x00\x00" + c.compress(fb) + c.flush()
            else:
                body = b"hxr" + fb
            pad = int(os.environ.get("GUL_PAD", "0"))
            if pad and op == "doHandshake":
                body = body + b"x" * pad  # truncation probe: never read if the value parses first
            slog(f"    REPLY {op} -> {body[:300]!r}")
            self._reply(body, ctype="application/vnd.haxe.remoting")
            return
        # non-remoting web legs
        if p.endswith("/auth/check"):
            # R789: the client JSON-parses this body and reads .body as the JWT. A bare JWT gave
            # 'Error while parsing json response: x.eyJ1c2VyIjoiZGV2dXNlciJ9.z for "auth/check?state=..."'
            # (LoginGameMode.hx:604) and then Result:AuthError. Wrap it.
            ident = current_identity()
            payload = {"body": ident["jwt"]}
            if os.environ.get("GUL_AUTH_ACCOUNTS", "1") == "1":
                # The auth check is where a client learns *which accounts* it can log in as (the client
                # has checkForExistingTwitchAccount, onLinkAccountAuth and "requires account" paths), and
                # the session context's accounts are exactly that list. Without them the session context
                # has no accounts at all, and every read of the context's account field misses.
                accts = [_account_record(ident)]
                payload.update({
                    "accounts": accts, "m_accounts": list(accts),
                    "provider": "twitch", "providerString": "twitch", "id": ident["iid"],
                    "accountID": ident["accountID"], "displayName": ident["name"],
                    "playerIID": ident["iid"],
                    "pgsToken": ident["jwt"],
                })
            slog("    (auth/check -> JSON with the account records)")
            self._reply(json.dumps(payload).encode(), ctype="application/json")
            return
        if "/games/streamline/active_user" in p or p.rstrip("/").endswith("/games/streamline/active_user"):
            # PGSManager.sendClientHeartbeat (FUN_140c1e140/FUN_140c19d10) requires HTTP success
            # and a numeric interval_sec > 0; PGS_PARTY here produced the R937 malformed response.
            slog("    (active_user -> heartbeat interval JSON)")
            self._reply(json.dumps({"interval_sec": int(os.environ.get("GUL_HEARTBEAT_SEC", "30"))}).encode(),
                        ctype="application/json")
            return
        if "/updates/" in p:
            # SLGameInstance.startWatchingUser -> ModelWatcher: a non-empty users array with a
            # non-null first user is required; an empty/missing array is an error and causes the
            # observed 10 s restart loop. update_stamp advances the cursor, and the endpoint is a
            # long-poll: without a server-side hold the client immediately re-polls the same stamp
            # in a busy loop.
            #
            # R1020: an *advancing* stamp is also the clock for the client's per-update work. Each
            # delivered user update runs LoginGameMode.onUserUpdate, which cancels the previous
            # Twitch broadcast check and starts a new one; if updates arrive faster than the HTTPS
            # round trip, every check is cancelled and logs "HttpClient.hx:144: Ignoring successful
            # request because it was cancelled. url=https://api.twitch.tv/kraken/streams/<user>"
            # (the R1018 storm). GUL_USER_WATCH_STAMP=echo returns the requested stamp so no update
            # is delivered, and GUL_USER_WATCH_HOLD bounds the re-poll rate at the same time.
            hold = float(os.environ.get("GUL_USER_WATCH_HOLD", os.environ.get("GUL_USER_POLL_DELAY", "0")))
            if hold > 0:
                time.sleep(hold)
            try:
                requested = int(p.rsplit("/", 1)[-1])
            except ValueError:
                requested = -1
            if os.environ.get("GUL_USER_WATCH_STAMP", "advance").lower() == "echo":
                next_stamp = max(1, requested)
            else:
                next_stamp = max(1, requested + 1)
            ident = current_identity()
            iid = ident["iid"]
            user = {
                "update_stamp": next_stamp,
                "id": iid,
                "playerIID": iid,
                "accountID": ident["accountID"],
                "displayName": ident["name"],
                "provider": "twitch",
                "providerString": "twitch",
                "active_party": "",
            }
            slog(f"    (user watcher {p} -> users[{iid}] stamp={next_stamp})")
            self._reply(json.dumps({"users": [user]}).encode(), ctype="application/json")
            return
        if p.rstrip("/").endswith("/channels"):
            # TopLiveGamesWidget.updateFromPGS: empty live_channels is safe.
            slog("    (channels -> empty live_channels)")
            self._reply(json.dumps({"live_channels": []}).encode(), ctype="application/json")
            return
        if "/parties" in p or "active_party" in p or "/games/" in p or "/wagers" in p or "/polls" in p:
            slog(f"    (PGS leg {p} -> party JSON)")
            self._reply(json.dumps(pgs_party()).encode(), ctype="application/json")
            return
        if "/remoting-config" in p and os.environ.get("GUL_RCONF"):
            # The client's own field cluster puts /remoting-config beside playerIID, pgsToken,
            # accounts, displayName, accountID, provider - i.e. this leg is where the session context
            # (and its accounts) is fetched. Answer it with a JSON session context rather than {}.
            slog("    (remoting-config -> JSON session context with accounts)")
            ident = current_identity()
            ctx = {
                "playerIID": ident["iid"],
                "accounts": [_account_record(ident)],
                "pgsToken": ident["jwt"],
                "displayName": ident["name"],
                "accountID": ident["accountID"],
                "provider": "twitch",
            }
            self._reply(json.dumps(ctx).encode(), ctype="application/json")
            return
        # Round 20: answer unknown legs with empty JSON — region pings / config fetches
        # may be parsed downstream (an empty body would throw in a JSON parser).
        slog(f"    (non-remoting leg {p!r} — answering {{}} JSON)")
        self._reply(json.dumps({}).encode(), ctype="application/json")

    do_GET = do_POST = do_PUT = _handle

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    slog(f"--- mock_appserver (haxe.remoting speaky) on :{port} ({time.ctime()})")
    ThreadingHTTPServer((os.environ.get("GUL_BIND", "127.0.0.1"), port), Handler).serve_forever()
