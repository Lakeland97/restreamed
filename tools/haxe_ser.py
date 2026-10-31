#!/usr/bin/env python3
import os
"""Minimal Haxe Serializer/Unserializer (the std format used by haxe.remoting).

Grammar reference: haxe/Serializer.hx (Haxe 3/4 std — token table unchanged).
Validated against real captured Streamline request bodies (see self_test).

Tokens implemented: n(null) t/f(bool) z(0) i(int) d(float) k/m/p(nan/inf)
y(urlencoded string + string table) R(string table ref) o...g(object)
a...h(array, u<n>+n null-runs) r(obj ref, not emitted by us) x(exception)
b...h(StringMap). Enough for Streamline appserver traffic.
"""
import urllib.parse

# Haxe StringTools.urlEncode safe set ~= encodeURIComponent: A-Za-z0-9-_.!~*'()
_HAXE_SAFE = "-_.!~*'()"


def _haxe_urlencode(s: str) -> str:
    return urllib.parse.quote(s, safe=_HAXE_SAFE)


def serialize(value) -> str:
    return Serializer().value(value)


def handshake_response(config) -> str:
    """Full Round-5 envelope: value1 = V-type-declaration (fills the shared type table),
    value2 = Result.Success(DataStoreUpdateResponse{updatedEntities:[], entityTypeInfo:"",
    returnVal=config}) — branch A passes returnVal through to the original callback."""
    s = Serializer()
    # Round 9/10: base/version/custom are i-form; fieldCount is RAW DIGITS (the reader uses a
    # digit loop, then requires ':' — "Invalid type format (fields)" otherwise).
    # Round 22: DataStoreClient's Success consumer (FUN_1411f7750) __RTDynamicCasts the payload to
    # appserver::remoting::DataStoreUpdateResponse_obj and reads updatedEntities(+8)/entityTypeInfo
    # /returnVal(+0x20). Branch A = updatedEntities.length<1 -> deliver Success(returnVal) unchanged.
    # A bare enum (Round-21 experiment) fails that cast -> NULL -> [NULL+8] AV -> silent stall.
    # Round 692 (V-reader FUN_141036c60:346): after parsing, the reader walks the BASE-CLASS
    # chain: while (type+0x40==0 && type+0x48==0) { base = type+0x28; if (base==0xffffffff)
    # exit; walk typeTable[base] }. base is read from the wire when param_1+8 >= 1 (the normal
    # case for our responses — a wire "i0" base otherwise misaligns and throws "(fields)",
    # which never happened). base=0 with our decl being typeTable[0] (fresh per-response
    # table) walks itself forever -> the silent CPU-spinning hang every V-decl body had.
    # base=-1 is the walk's exit sentinel, so i-1 terminates it.
    decl = ("V" + s.value("appserver.remoting.DataStoreUpdateResponse")
            + ":i-1:i0:i0:3:"
            + s.value("updatedEntities") + s.value("entityTypeInfo") + s.value("returnVal")
            + ":")
    inst = "T0:" + "ah" + s.value("") + s.value(config) + "g"
    result = "w" + s.value("model.Result") + s.value("Success") + ":1" + inst
    return decl + result


def handshake_decl_str(payload) -> str:
    """Round 708 discriminator: value1 = the identical i-1 V-decl, value2 =
    Result.Success(string payload) — proven parseable by the success1 probe when it
    follows 'n'. Separates the two candidates behind the i-1 body's "Invalid enum
    format": a payload-cast AV (0x1411F781E, no :493) means the decl/value boundary is
    clean and the T0/config instance of handshake_response was the misalignment; a
    :493 "Invalid enum format" means the decl corrupts the value after it (the
    V-reader tail's version-info contract, FUN_141036c60:373+)."""
    s = Serializer()
    decl = ("V" + s.value("appserver.remoting.DataStoreUpdateResponse")
            + ":i-1:i0:i0:3:"
            + s.value("updatedEntities") + s.value("entityTypeInfo") + s.value("returnVal")
            + ":")
    return decl + "w" + s.value("model.Result") + s.value("Success") + ":1" + s.value(payload)


def handshake_decln_str(payload) -> str:
    """Round 714 structure probe: [i-1 decl][explicit n echo slot][Success(string)].
    decistr showed [decl][Success] ends in an EOF over-read while [n][Success] delivers,
    i.e. a leading decl apparently does not occupy a value slot — this run inserts the
    null echo slot explicitly. AV at 0x1411F781E = slot2 delivered (payload cast) ->
    structure confirmed, next run swaps in the T0 DSUR instance; AV at 0x1411F7766 =
    consumer got null (decl+n were the two slots, Success unread); Invalid char = the
    slot count is still off; enum-format = the T0/config branch after all."""
    s = Serializer()
    decl = ("V" + s.value("appserver.remoting.DataStoreUpdateResponse")
            + ":i-1:i0:i0:3:"
            + s.value("updatedEntities") + s.value("entityTypeInfo") + s.value("returnVal")
            + ":")
    return (decl + "n"
            + "w" + s.value("model.Result") + s.value("Success") + ":1" + s.value(payload))


def handshake_decln_trail(payload) -> str:
    """Round 716 position-probe: [decl][n][Success(string)][n][@]. The trailing 'n' tests
    Round-4's 'a valid trailing value completes the parse' directly; the trailing '@' makes
    any further stray dispatch VISIBLE with 'Invalid char @ at position P' instead of the
    \\0-truncated form (P then says whether reads overran exactly past the body or
    derailed at an interior byte). AV 0x1411F781E = structure solved."""
    s = Serializer()
    decl = ("V" + s.value("appserver.remoting.DataStoreUpdateResponse")
            + ":i-1:i0:i0:3:"
            + s.value("updatedEntities") + s.value("entityTypeInfo") + s.value("returnVal")
            + ":")
    return (decl + "n"
            + "w" + s.value("model.Result") + s.value("Success") + ":1" + s.value(payload)
            + "n@")


def handshake_decl_enum_inst(config) -> str:
    """Round 723: [i-1 decl][n][Success(string)][tokenless DSUR instance: fields + 'g'].
    FUN_1410399a0 (the class-instance reader, called from the T-reader and the dispatcher)
    reads N field values (N = the class's field count, 3 for our decl) then REQUIRES 'g'.
    decltrail failed exactly at the 'g' check, positioned on the trailing marker byte, and
    the no-marker runs die in the instance's field reads at EOF — so the value the reader
    wants after the enum is a plain DSUR instance. Minimal delta from decltrail."""
    s = Serializer()
    decl = ("V" + s.value("appserver.remoting.DataStoreUpdateResponse")
            + ":i-1:i0:i0:3:"
            + s.value("updatedEntities") + s.value("entityTypeInfo") + s.value("returnVal")
            + ":")
    enum = "w" + s.value("model.Result") + s.value("Success") + ":1" + s.value("PROBE123")
    inst = "ah" + s.value("") + s.value(config) + "g"
    return decl + "n" + enum + inst


def dsur_branch_b(config, entities_value, entity_blob, extra_classes=()) -> str:
    """[n][Result.Success(<DSUR instance, NON-EMPTY updatedEntities>)] — the DataStore branch B.

    DECOMPILE_NOTES R5: the consumer tests updatedEntities.length < 1 -> branch A, which simply
    delivers Success(returnVal). With a non-empty list it takes branch B, where entityTypeInfo (an
    inline String) is handed to StringInflateStream and inflated to ONE value ("pushed entity
    updates"). `entities_value` is what the client sees as updatedEntities (only its LENGTH matters
    for the branch decision); `entity_blob` is the serialized text placed in entityTypeInfo.
    Both are passed in so the shapes can be iterated from the environment without code edits.
    """
    s = Serializer()
    for c in extra_classes:            # declare referenced types first, so their indices start at 0
        s.type_index(c)
    # The wire order is [Result enum][declarations][payload], and the client builds its string
    # table in that same order. Serialize the enum strings before the payload so R<n> indices
    # cannot be shifted by the payload's first-seen strings (the `accounts` bug).
    prefix = "n" + "w" + s.value("model.Result") + s.value("Success") + ":1"
    decls = s.decl_fragment()          # any classes registered before this call, in index order
    payload = ("V" + s.value("appserver.remoting.DataStoreUpdateResponse")
               + ":i-1:i0:i0:3:"
               + s.value("updatedEntities") + s.value("entityTypeInfo") + s.value("returnVal")
               + ":"
               + s.value(entities_value) + s.value(entity_blob) + s.value(config) + "g")
    return prefix + decls + payload


def result_success(value) -> str:
    """[n][Result.Success(<value>)] — the payload IS the value, with no DSUR wrapper around it.

    The appserver's DSUR envelope puts the interesting object in its `returnVal` field, but the client
    code that builds the local player reads its fields (accounts, pgsToken, displayName, playerIID,
    accountID, provider, session) straight off the *payload* of the Result it is handed. An anonymous
    object serialized as the payload is exactly the shape those named reads want.
    """
    s = Serializer()
    return "n" + "w" + s.value("model.Result") + s.value("Success") + ":1" + s.value(value)


def handshake_result_dsur(config) -> str:
    """Round 726: [n][Result.Success(<self-contained V-form DSUR instance>)].

    FUN_1410369c0 dispatches the T/V/W group ('T'=class by index + ':', 'V'=declaration via
    FUN_141036c60(...,1), 'W'=walk variant) and the dispatcher (FUN_141039c50 case 0x54/0x56/0x57)
    ALWAYS follows the type read with FUN_1410399a0 = the instance body: N = class+0x38 field
    values, then a REQUIRED 'g'. So a V token is ONE value of the form [V][decl][N values][g].
    Every earlier decl body was 'decl then more values', so the reader consumed those as the
    instance's 3 fields and failed the 'g' check one value later (decoraltrail pos 152='@',
    declinst pos 153='Y'). Here DSUR is written self-contained as the Success payload:
    updatedEntities=[] (branch A short-circuit), entityTypeInfo="", returnVal=config.
    """
    s = Serializer()
    prefix = "n" + "w" + s.value("model.Result") + s.value("Success") + ":1"
    payload = ("V" + s.value("appserver.remoting.DataStoreUpdateResponse")
               + ":i-1:i0:i0:3:"
               + s.value("updatedEntities") + s.value("entityTypeInfo") + s.value("returnVal")
               + ":"
               + "ah" + s.value("") + s.value(config) + "g")
    return prefix + payload


def handshake_decl_plus_t0(config) -> str:
    """Round 726b: [<V-form DSUR>][Result.Success(<T0 DSUR>)] — fallback shape in case the
    payload must be the T-form referencing the declaration registered by value 1. The decl
    puts DSUR in the class table (param_1+0x48) at index 0, and T0 references it."""
    s = Serializer()
    decl_value = ("V" + s.value("appserver.remoting.DataStoreUpdateResponse")
                  + ":i-1:i0:i0:3:"
                  + s.value("updatedEntities") + s.value("entityTypeInfo") + s.value("returnVal")
                  + ":"
                  + "ah" + s.value("") + s.value(config) + "g")
    tpayload = "T0:" + "ah" + s.value("") + s.value(config) + "g"
    enum = "w" + s.value("model.Result") + s.value("Success") + ":1" + tpayload
    return decl_value + enum


def result_success(payload) -> str:
    """[path-echo null][model.Result.Success(payload)] — the callback consumes the SECOND value
    (wire-trace proof: the callback consumes value 2; request is path+params, response
    is echo+result)."""
    s = Serializer()
    return "n" + "w" + s.value("model.Result") + s.value("Success") + ":1" + s.value(payload)


def result_error(message) -> str:
    s = Serializer()
    return "w" + s.value("model.Result") + s.value("Error") + ":1" + s.value(message)


class HxEnum:
    """Parameterless enum VALUE for the fork's serializer (j-form: name : idx : argc)."""

    def __init__(self, name, idx, args=None):
        self.name = name
        self.idx = idx
        self.args = args or []


class HxClass:
    """A haxe class that can be referenced in a stream: a name plus ordered field names.

    The client assigns instance values POSITIONALLY (decompiled: "T<idx>: + positional
    field values + g"), so the declared names are metadata for the reader's type table while the
    ORDER is what lands values in the real class's fields.
    """

    def __init__(self, name: str, fields=()):
        self.name = name
        self.fields = list(fields)


class HxInstance:
    """A positional instance of an HxClass, for a stream-level `T<idx>:` reference."""

    def __init__(self, cls, values=()):
        self.cls = cls if isinstance(cls, HxClass) else HxClass(cls)
        self.values = list(values)


class RawFragment(str):
    """Wire text injected verbatim as a value (not re-escaped as a String).

    Needed for hand-built values like `T1:nY7:ZVER3qhng`: without this it would be wrapped as
    `Y19:...`, i.e. a String where the client expects an instance, which makes it throw.
    """


class Serializer:
    """Haxe Serializer with a persistent string table (needed across enum+payload)."""

    def __init__(self):
        self.strtab: list = []
        self.decls: list = []          # HxClass, in declaration (= type index) order
        self._typeidx: dict = {}

    def type_index(self, cls) -> int:
        """Register a class for declaration and return its stream type index."""
        if not isinstance(cls, HxClass):
            cls = HxClass(cls)
        if cls.name not in self._typeidx:
            self._typeidx[cls.name] = len(self.decls)
            self.decls.append(cls)
        return self._typeidx[cls.name]

    def decl_fragment(self) -> str:
        """The accumulated `V` declarations, mirroring the proven-working DSUR decl form."""
        if not self.decls:
            return ""
        return "".join(
            "V" + self._ser_str(c.name) + ":i-1:i0:i0:" + str(len(c.fields)) + ":"
            + "".join(self._ser_str(f) for f in c.fields) + ":"
            for c in self.decls)

    def _ser_str(self, s: str) -> str:
        # GUL_SER_NOREF=1 disables string back-references, so every string is written out in full. A
        # mis-indexed or unsupported R would shift a client's parse by exactly one token, which is the
        # failure shape currently being chased; this isolates the reference mechanism as the variable.
        if os.environ.get("GUL_SER_NOREF") == "1":
            self.strtab.append(s)
            return f"Y{len(s)}:{s}"
        for i, prev in enumerate(self.strtab):
            if prev == s:
                # Round 23 CORRECTION: the client's R reader is 0-BASED (DECOMPILE_NOTES L1536/1580:
                # `table[uVar3]`, bounds `uVar3 < table.size`, "R 0-based — high confidence, verbatim
                # decompile line 341"). Python strtab is also 0-based, so R{i} is CORRECT. An earlier
                # "off-by-one fix" (R{i+1}) was WRONG and is reverted — it would break every R-ref.
                return f"R{i}"
        self.strtab.append(s)
        # Round 12 (byte-proven): this build's 'Y' reads RAW length + RAW bytes (no decode);
        # 'y' expects ENCODED length + url-decoded content — the length/urlencoding coupling is a
        # trap. Y only.
        return f"Y{len(s)}:{s}"

    def value(self, v) -> str:
        if isinstance(v, RawFragment):
            return str(v)          # verbatim wire text: MUST precede the str branch
        if isinstance(v, HxEnum):
            # Round 727 (decompile FUN_14103dc20): both enum paths fall through to LAB_14103e406,
            # which reads a byte and REQUIRES ':' ("Invalid enum format"), then a digit loop =
            # ARGC, then the args (FUN_141037bd0). So the 'j' (old/index) form is
            #   j <name> : <ctor-index> : <argc> <args...>
            # The old ':index' with no tail made the reader hit the config's next key where it
            # wanted ':' — the source of every "Invalid enum format" (all such runs contained
            # the DRMType config enum; no config-free run ever produced it).
            # GUL_J_TAIL=none restores the Round-16 "no tail" shape for A/B.
            out = "j" + self._ser_str(v.name) + f":{v.idx}"
            if os.environ.get("GUL_J_TAIL", "argc") != "none":
                out += f":{len(v.args)}"
            for a in v.args:
                out += self.value(a)
            return out
        if v is None:
            return "n"
        if isinstance(v, bool):
            return "t" if v else "f"
        if isinstance(v, int):
            # NOTE: emit `i0`, never `z`, for zero. The mock's own proven DSUR payload hardcodes
            # ":i-1:i0:i0:" rather than letting this function write the zeros, and the session context's
            # `timeOffset: 0` is the first zero that ever travelled *inside* an object the client parses -
            # where the failure reports an enum name taken from the *next* token, i.e. a one-token
            # desynchronisation. GUL_SER_Z=1 restores the old `z` form for A/B.
            if v == 0 and os.environ.get("GUL_SER_Z") == "1":
                return "z"
            return f"i{v}"
        if isinstance(v, float):
            if v != v:
                return "k"
            if v == float("inf"):
                return "p"
            if v == float("-inf"):
                return "m"
            return "d" + repr(v)
        if isinstance(v, str):
            return self._ser_str(v)
        if isinstance(v, (list, tuple)):
            out, nulls = ["a"], 0
            for item in v:
                if item is None:
                    nulls += 1
                else:
                    if nulls == 1:
                        out.append("n")
                    elif nulls > 1:
                        out.append(f"u{nulls}")
                    nulls = 0
                    out.append(self.value(item))
            if nulls == 1:
                out.append("n")
            elif nulls > 1:
                out.append(f"u{nulls}")
            out.append("h")
            return "".join(out)
        if isinstance(v, HxInstance):
            return "T" + str(self.type_index(v.cls)) + ":" + "".join(self.value(x) for x in v.values) + "g"
        if isinstance(v, dict):
            out = ["o"]
            for k, val in v.items():
                out.append(self._ser_str(str(k)))
                out.append(self.value(val))
            out.append("g")
            return "".join(out)
        raise TypeError(f"cannot serialize {type(v)}")


def serialize_exception(msg: str) -> str:
    return "x" + serialize(msg)


class Unserializer:
    def __init__(self, data: str):
        self.d = data
        self.i = 0
        self.strtab: list = []

    def _read_until(self, ch: str) -> str:
        j = self.d.index(ch, self.i)
        s = self.d[self.i:j]
        self.i = j + 1
        return s

    def unserialize(self):
        c = self.d[self.i]
        self.i += 1
        if c == "n":
            return None
        if c == "t":
            return True
        if c == "f":
            return False
        if c == "z":
            return 0
        if c == "i":
            return self._read_int_plain()
        if c == "d":
            return self._read_float()
        if c == "k":
            return float("nan")
        if c == "m":
            return float("-inf")
        if c == "p":
            return float("inf")
        if c in ("y", "Y"):
            ln = int(self._read_until(":"))
            raw = self.d[self.i:self.i + ln]
            self.i += ln
            s = urllib.parse.unquote(raw) if c == "y" else raw
            self.strtab.append(s)
            return s
        if c == "R":
            idx = self._read_int_plain()
            return self.strtab[idx]
        if c == "o":
            obj = {}
            while True:
                if self.d[self.i] == "g":
                    self.i += 1
                    break
                key = self.unserialize()
                obj[key] = self.unserialize()
            return obj
        if c == "a":
            arr = []
            while True:
                if self.d[self.i] == "h":
                    self.i += 1
                    break
                if self.d[self.i] == "u":
                    self.i += 1
                    n = self._read_int_plain()
                    arr.extend([None] * n)
                    continue
                arr.append(self.unserialize())
            return arr
        if c == "b":
            m = {}
            while self.d[self.i] != "h":
                k = self.unserialize()
                m[k] = self.unserialize()
            self.i += 1
            return m
        if c == "x":
            return {"__exception__": self.unserialize()}
        if c == "j":
            # Haxe 3 enum-by-name where the type name is itself a serialized string:
            #   j <string name> : <constructor index> : <arg count> [args...]
            # Captured equipCosmeticItems body: "j y31:model.game.seek.CharacterGender : 1 : 0".
            # `w` (raw name, no nested string) is the other by-name form; both appear in UE4-era
            # clients, so the token must be handled or every enum-bearing request logs DECODE FAILED.
            name = self.unserialize()
            if self.d[self.i] == ":":
                self.i += 1
            index = self._read_int_plain()
            if self.d[self.i] == ":":
                self.i += 1
            argc = self._read_int_plain()
            args = [self.unserialize() for _ in range(argc)]
            return {"__enum__": name, "index": index, "args": args}
        raise ValueError(f"unserialize: unknown token {c!r} at {self.i - 1} in {self.d[:80]!r}")

    def _read_int_plain(self) -> int:
        j = self.i
        if j < len(self.d) and self.d[j] == "-":
            j += 1
        while j < len(self.d) and self.d[j].isdigit():
            j += 1
        v = int(self.d[self.i:j])
        self.i = j
        return v

    def _read_int(self) -> int:
        # int ends at next token char — Haxe ints are digits until a non-digit
        return self._read_int_plain()

    def _read_float(self) -> float:
        j = self.i
        while j < len(self.d) and (self.d[j].isdigit() or self.d[j] in ".eE+-"):
            j += 1
        v = float(self.d[self.i:j])
        self.i = j
        return v


def unserialize(data: str):
    return Unserializer(data).unserialize()


def unserialize_two(data: str):
    """Requests carry path + params concatenated (HttpConnection.call format)."""
    u = Unserializer(data)
    a = u.unserialize()
    b = u.unserialize()
    return a, b


def self_test():
    # Captured Streamline postTrackingInfo body (decoded from form-encoding):
    body1 = ("nano y12:login_statusy11:Handshakingy8:match_idy0:y5:wherey4:none"
             "y5:_wheni821309813y10:channel_idR3y6:_tabley10:streamliney11:tracking_idR3"
             "y10:session_idR3y21:streamline_session_idy32:387C92174B1A83093F7887824881AF10"
             "y6:_eventy11:login_stategh").replace(" ", "")
    path, params = unserialize_two(body1)
    assert path is None, path
    assert isinstance(params, list) and len(params) == 2, params
    obj = params[1]
    assert obj["login_status"] == "Handshaking"
    assert obj["match_id"] == "" and obj["channel_id"] == "" and obj["session_id"] == ""
    assert obj["_when"] == 821309813
    assert obj["streamline_session_id"] == "387C92174B1A83093F7887824881AF10"
    # Captured doHandshake body:
    path2, params2 = unserialize_two("nah")
    assert path2 is None and params2 == [], (path2, params2)
    # round-trip
    sample = {"a": "x", "b": [1, None, "two"], "c": None, "d": 821309813, "e": ""}
    assert unserialize(serialize(sample)) == sample
    assert unserialize(serialize(sample))["b"][2] == "two"
    # string table ref behavior
    s = serialize(["same", "same", "other"])
    assert "R0" in s, s
    # enum-by-name with a serialized string name (the `j` token). Captured equipCosmeticItems body:
    path3, params3 = unserialize_two(
        "najy31:model.game.seek.CharacterGender:1:0y14:skintone_threeahahh")
    assert path3 is None and params3 == [
        {"__enum__": "model.game.seek.CharacterGender", "index": 1, "args": []},
        "skintone_three", [], []], params3
    # enum with one argument (DRMType.FakeID carries the account string)
    _, params4 = unserialize_two(
        "naoy17:trackingSessionIDy32:107F70544DC5B2029BAE59B8F38F17BF"
        "y3:drmjy25:model.service.drm.DRMType:3:1y8:restrea1y11:countryCodey0:gh")
    drm = params4[0]["drm"]
    assert drm["__enum__"] == "model.service.drm.DRMType" and drm["index"] == 3, drm
    assert drm["args"] == ["restrea1"], drm
    # negative ints are `i-<digits>` (the old reader only consumed digits)
    assert unserialize("i-1") == -1
    print("haxe_ser self_test: OK")
    print(f"  body1 path={path!r} params_len={len(params)}")
    print(f"  handshake sample -> {serialize(sample)[:60]}...")


if __name__ == "__main__":
    self_test()
