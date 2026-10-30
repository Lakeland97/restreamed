#!/usr/bin/env python3
"""hxInflate streams — the serialization format the appserver speaks.

Spec (authoritative, from the library the client links): proletariatgames/hxinflate
(haxelib `hxInflate`), `serialization/Deflater.hx` (writer) and `serialization/Inflater.hx`
(reader). VERSION = 3, VERSION_CODE = "ZVER".

How the appserver reply uses it (verified against the client's own inflater):

  entityTypeInfo   = the *external type cache* stream: "ZVER3" + purpose + type-info tokens
                     (read by Inflater.inflateTypeInfo -> `new Inflater(stream)`, header REQUIRED)
  updatedEntities  = Array<String>, one serialized object per element, each read with
                     {typeInflater: <the cache>, skipHeader: true}: purpose + value, NO "ZVER"

Grammar (writer side):

  "<ZVER><version>"                       only when the header is written
  value(options.purpose)                  always; the reader reads it when version >= 2
  values / type-info tokens

  type info : V final type, W base type, T<idx>: cached type ref, | enum info, = enum value info,
              _<idx>: cached enum value, Y raw string, R<idx> string-table ref, S field skipped (v3)
  values    : n null, t/f bool, z int 0, i<int>, d<float>, k/m/p NaN/-inf/+inf,
              y<len>:<urlencoded> string (R<idx> refs), a..h array (u<n> null runs), o..g object,
              q..h IntMap (keys are RAW digits after ':'), b..h StringMap, l..h list, r<idx> ref,
              s bytes, v date, w/j enum, x exception

  class declaration (writeClassInfo):
      serializeString(name) ":" serialize(base) ":" serialize(version) ":" serialize(custom) ":"
      if (!custom) <numFields as raw digits> ":" serializeString(field)* ":"
  the instance body (one value per field, or the class's own hxSerialize output) follows and ends
  with "g". A class with hxSerialize is custom=true and carries NO field list.
"""
import urllib.parse

VERSION_CODE = "ZVER"
VERSION = 3
_SAFE = "-_.!~*'()"


class Raw(str):
    """Wire text injected verbatim as a value (already in stream grammar)."""


def _enc(s: str) -> str:
    # haxe StringTools.urlEncode == encodeURIComponent-ish with this safe set
    return urllib.parse.quote(s, safe=_SAFE)


class Decl:
    """A class declaration for a type-info stream."""

    def __init__(self, name, base=-1, version=0, custom=False, fields=()):
        self.name = name
        self.base = base
        self.version = version
        self.custom = custom
        self.fields = list(fields)


class Instance:
    """A class instance. Either declares its class (`decl`) or references a cached one (`idx`)."""

    def __init__(self, values=(), decl=None, idx=None):
        self.values = list(values)
        self.decl = decl
        self.idx = idx


class Writer:
    def __init__(self, header=True, purpose=None):
        self.parts = []
        self.table = {}
        if header:
            self.parts.append(VERSION_CODE + str(VERSION))
        self.value(purpose)

    # ---- primitives ------------------------------------------------------
    def _string(self, s):
        if s in self.table:                     # Deflater.serializeString: table + R<idx>
            self.parts.append("R%d" % self.table[s])
            return
        self.table[s] = len(self.table)
        e = _enc(s)
        self.parts.append("y%d:%s" % (len(e), e))

    def value(self, v):
        if isinstance(v, Raw):
            self.parts.append(str(v))
        elif v is None:
            self.parts.append("n")
        elif v is True:
            self.parts.append("t")
        elif v is False:
            self.parts.append("f")
        elif isinstance(v, int):
            self.parts.append("z" if v == 0 else "i%d" % v)
        elif isinstance(v, float):
            if v != v:
                self.parts.append("k")
            elif v == float("inf"):
                self.parts.append("p")
            elif v == float("-inf"):
                self.parts.append("m")
            else:
                self.parts.append("d%r" % v)
        elif isinstance(v, str):
            self._string(v)
        elif isinstance(v, Instance):
            self.instance(v)
        elif isinstance(v, (list, tuple)):
            self.array(v)
        elif isinstance(v, dict):
            if v and all(isinstance(k, int) for k in v):
                self.int_map(v)
            else:
                self.str_map(v)
        else:
            raise TypeError("cannot serialize %r" % (v,))
        return self

    def array(self, items):
        self.parts.append("a")
        run = 0
        for it in items:
            if it is None:
                run += 1
                continue
            if run == 1:
                self.parts.append("n")
            elif run > 1:
                self.parts.append("u%d" % run)
            run = 0
            self.value(it)
        if run == 1:
            self.parts.append("n")
        elif run > 1:
            self.parts.append("u%d" % run)
        self.parts.append("h")
        return self

    def int_map(self, d):
        self.parts.append("q")
        for k, v in d.items():
            self.parts.append(":%s" % k)        # RAW key, no 'i' prefix
            self.value(v)
        self.parts.append("h")
        return self

    def str_map(self, d):
        self.parts.append("b")
        for k, v in d.items():
            self._string(k)
            self.value(v)
        self.parts.append("h")
        return self

    def class_info(self, d):
        self._string(d.name)
        self.parts.append(":")
        self.value(d.base)
        self.parts.append(":")
        self.value(d.version)
        self.parts.append(":")
        self.value(bool(d.custom))
        self.parts.append(":")
        if not d.custom:
            self.parts.append("%d:" % len(d.fields))
            for f in d.fields:
                self._string(f)
            self.parts.append(":")
        return self

    # ---- type info / instances ------------------------------------------
    def typeinfo(self, d, final=True):
        """A declaration in a type-info stream: V (final) or W (base), no instance body."""
        self.parts.append("V" if final else "W")
        self.class_info(d)
        return self

    def instance(self, inst):
        if inst.decl is not None:
            self.parts.append("V")
            self.class_info(inst.decl)
        else:
            if inst.idx is None:
                raise ValueError("instance needs decl or idx")
            self.parts.append("T%d:" % inst.idx)
        for v in inst.values:
            self.value(v)
        self.parts.append("g")
        return self

    def text(self) -> str:
        return "".join(self.parts)


def type_cache(decls=(), purpose=None) -> str:
    """The entityTypeInfo string: a type-info stream with the ZVER header."""
    w = Writer(header=True, purpose=purpose)
    for d in decls:
        w.typeinfo(d, final=True)
    return w.text()


def object_stream(value, purpose=None) -> str:
    """One updatedEntities element: read with skipHeader, so no ZVER header, but a purpose value."""
    w = Writer(header=False, purpose=purpose)
    w.value(value)
    return w.text()


class Reader:
    """Minimal mirror of Inflater (serialization/Inflater.hx) for host-side verification."""

    def __init__(self, text, skip_header=False, radix_relative=True):
        self.s = text
        self.i = 0
        self.table = []
        self.types = []
        self.rnodes = [(None, 0)]  # radix tree: id 0 is the root, ids follow creation order
        self.rlast = 0
        self.radix_relative = radix_relative
        self.etypes = []
        self.trace = False         # set True to print each token with its position
        self.depth = 0
        if skip_header:
            self.version = VERSION
        elif self.s[self.i:self.i + 4] == VERSION_CODE:
            self.i += 4
            self.version = self._digits()
        else:
            self.version = 0
        self.purpose = self.value() if self.version >= 2 else None
        self.skip_code = "S" if self.version >= 3 else "E"

    def _digits(self):
        j = self.i
        if self.i < len(self.s) and self.s[self.i] == "-":
            self.i += 1
        while self.i < len(self.s) and self.s[self.i].isdigit():
            self.i += 1
        return int(self.s[j:self.i] or 0)

    # ---- radix tree (serialization/internal/RadixTree.hx) -----------------
    def radix(self, relative=True):
        """Read a `!`/`#`/`~` encoded string. With a typeInflater present the library resets the
        running offset on every call; otherwise the offset carries across calls."""
        if not relative:
            self.rlast = 0
        code = self.s[self.i]
        self.i += 1
        if code == "~":
            self.rlast = self._digits() + self.rlast
            return self._uptree(self.rlast)
        if code not in ("!", "#"):
            raise AssertionError("unrecognized radix code %r" % code)
        node = self._digits() + self.rlast
        self.rlast = node
        assert self.s[self.i] == ":", "invalid RadixTree code"
        self.i += 1
        if code == "#":
            num = self._digits()
            assert self.s[self.i] == ":", "invalid RadixTree code"
            self.i += 1
        else:
            num = None
        n = self._digits()
        assert self.s[self.i] == ":", "invalid string length"
        self.i += 1
        s = self.s[self.i:self.i + n]
        self.i += n
        if code == "!":
            # a new child of the node at `node`
            return self._uptree(self._new_node(s, node))
        # '#': split the node at `node`, keeping `num` chars and appending `s`
        old_data, old_parent = self.rnodes[node]
        self.rnodes[node] = (old_data[num:], old_parent)     # the existing node keeps the tail
        split = self._new_node(old_data[:num], old_parent)   # the split node
        suffix = self._new_node(s, split)                    # the new suffix hangs off the split
        self.rnodes[node] = (old_data[num:], split)          # reparent the existing node
        return self._uptree(suffix)

    def _new_node(self, data, parent):
        self.rnodes.append((data, parent))
        return len(self.rnodes) - 1

    # ---- enums (Inflater.inflateEnum / inflateEnumValue) -----------------
    def enum_info(self):
        assert self.s[self.i] == "|"
        self.i += 1
        name = self.value()
        assert self.s[self.i] == ":", "Invalid type format (enum name)"
        self.i += 1
        version = self._digits()
        assert self.s[self.i] == ":", "Invalid type format (enum version)"
        self.i += 1
        use_index = self.value()
        assert self.s[self.i] == ":", "Invalid type format (enum use index)"
        self.i += 1
        info = {"name": name, "version": version, "useIndex": use_index}
        self.etypes.append(info)
        return info

    def enum_value(self):
        c = self.s[self.i]
        if c == "|":
            self.enum_info()
            return self.enum_value()
        if c == "_":
            self.i += 1
            idx = self._digits()
            assert self.s[self.i] == ":", "Invalid enum type reference"
            self.i += 1
            info = self.etypes[idx]
        else:
            assert c == "=", "Invalid enum type"
            self.i += 1
            info = {"construct": self.value()}
            assert self.s[self.i] == ":", "Invalid type format (enum constructor)"
            self.i += 1
            info["typeIndex"] = self._digits()
            assert self.s[self.i] == ":", "Invalid type format (enum type index)"
            self.i += 1
            info["enumIndex"] = self._digits()
            assert self.s[self.i] == ":", "Invalid type format (enum index)"
            self.i += 1
            info["numParams"] = self._digits()
            assert self.s[self.i] == ":", "Invalid type format (enum num params)"
            self.i += 1
            self.etypes.append(info)
        params = [self.value() for _ in range(info.get("numParams", 0))]
        return {"enum": info.get("construct"), "params": params}

    def _uptree(self, node):
        out = []
        while node != 0:
            data, parent = self.rnodes[node]
            out.append(data)
            node = parent
        return "".join(reversed(out))

    def value(self):
        c = self.s[self.i]
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
            return self._digits()
        if c == "d":
            j = self.i
            while self.i < len(self.s) and self.s[self.i] in "+-.,eE0123456789":
                self.i += 1
            return float(self.s[j:self.i])
        if c in ("k", "m", "p"):
            return {"k": float("nan"), "m": float("-inf"), "p": float("inf")}[c]
        if c == "y":
            n = self._digits()
            assert self.s[self.i] == ":", (self.i, self.s[self.i:self.i + 5])
            self.i += 1
            raw = self.s[self.i:self.i + n]
            self.i += n
            v = urllib.parse.unquote(raw)
            self.table.append(v)
            return v
        if c == "R":
            return self.table[self._digits()]
        if c in ("!", "#", "~"):
            self.i -= 1
            return self.radix(relative=self.radix_relative)
        if c in ("|", "=", "_"):
            self.i -= 1
            return self.enum_value()
        if c == "Y":
            n = self._digits()
            assert self.s[self.i] == ":", "Invalid string length"
            self.i += 1
            v = self.s[self.i:self.i + n]
            self.i += n
            self.table.append(v)
            return v
        if c == "s":                                   # bytes (base64-ish)
            n = self._digits()
            assert self.s[self.i] == ":", "Invalid bytes length"
            self.i += 1
            v = self.s[self.i:self.i + n]
            self.i += n
            return {"bytes": v}
        if c == "v":                                   # date
            v = self.s[self.i:self.i + 19]
            self.i += 19
            return {"date": v}
        if c == "r":                                   # object reference
            return {"ref": self._digits()}
        if c == "l":                                   # list
            out = []
            while self.s[self.i] != "h":
                out.append(self.value())
            self.i += 1
            return out
        if c in ("M", "N"):                            # object / enum-value maps
            out = []
            while self.s[self.i] != "h":
                out.append((self.value(), self.value()))
            self.i += 1
            return {"map": c, "pairs": out}
        if c == "x":                                   # exception (value carries it)
            return {"exception": self.value()}
        if c == "C":                                   # custom serializer
            name = self.value()
            body = []
            while self.s[self.i] != "g":
                body.append(self.value())
            self.i += 1
            return {"custom": name, "body": body}
        if c == "j":
            self.i -= 1
            return self.old_enum_value(True)
        if c == "a":
            out = []
            while self.s[self.i] != "h":
                if self.s[self.i] == "u":
                    self.i += 1
                    out.extend([None] * self._digits())
                else:
                    out.append(self.value())
            self.i += 1
            return out
        if c == "q":
            out = {}
            while self.s[self.i] == ":":
                self.i += 1
                k = self._digits()
                out[k] = self.value()
            assert self.s[self.i] == "h", "Invalid IntMap format"
            self.i += 1
            return out
        if c == "b":
            out = {}
            while self.s[self.i] != "h":
                k = self.value()
                out[k] = self.value()
            self.i += 1
            return out
        if c == "o":
            out = {}
            while self.s[self.i] != "g":
                k = self.value()
                out[k] = self.value()
            self.i += 1
            return out
        if c in ("V", "W"):
            self.i -= 1
            d = self.class_info(self.s[self.i] == "V")
            body = []
            while self.s[self.i] != "g":
                if self.s[self.i] == self.skip_code:
                    self.i += 1
                    body.append("<skip>")
                else:
                    body.append(self.value())
            self.i += 1
            return {"decl": d, "body": body}
        if c == "T":
            idx = self._digits()
            assert self.s[self.i] == ":"
            self.i += 1
            body = []
            while self.s[self.i] != "g":
                body.append(self.value())
            self.i += 1
            return {"type": idx, "body": body}
        raise AssertionError("Invalid char %s at position %d" % (c, self.i - 1))

    def class_info(self, final):
        assert self.s[self.i] in ("V", "W")
        final = self.s[self.i] == "V"
        self.i += 1
        d = Decl(self._read_string())
        assert self.s[self.i] == ":"
        self.i += 1
        d.base = self.value()
        assert self.s[self.i] == ":"
        self.i += 1
        d.version = self.value()
        assert self.s[self.i] == ":"
        self.i += 1
        d.custom = self.value()
        assert self.s[self.i] == ":"
        self.i += 1
        if not d.custom:
            n = self._digits()                       # raw digit count, then ':'
            assert self.s[self.i] == ":", self.s[self.i - 4:self.i + 4]
            self.i += 1
            d.fields = [self._read_string() for _ in range(n)]
            assert self.s[self.i] == ":"
            self.i += 1
        if final is not None:
            self.types.append(d)
        return d

    def _read_string(self):
        return self.value()

    def read_typeinfo_loop(self):
        """Mirror Inflater.inflateTypeInfo: the rest of a type-info stream must be declarations."""
        while self.i < len(self.s):
            c = self.s[self.i]
            if c in ("V", "W"):
                self.class_info(c == "V")
            elif c == "Y":
                self.i += 1
                n = self._digits()
                assert self.s[self.i] == ":"
                self.i += 1
                self.table.append(self.s[self.i:self.i + n])
                self.i += n
            else:
                raise AssertionError("unexpected code in typeinfo: %s" % c)
        return self.types


def _self_test():
    ent = Decl("model.entity.ModelEntity", base=-1, version=0, custom=True)
    cache = type_cache([ent], purpose=None)
    assert cache == "ZVER3nVy24:model.entity.ModelEntity:i-1:z:t:", cache
    r = Reader(cache)
    assert r.version == 3 and r.purpose is None, (r.version, r.purpose)
    types = r.read_typeinfo_loop()
    assert len(types) == 1 and types[0].name == "model.entity.ModelEntity" and types[0].custom, types

    body = object_stream(Instance(values=[1, "x"], decl=ent))
    assert body == "nVy24:model.entity.ModelEntity:i-1:z:t:i1y1:xg", body
    r2 = Reader(body, skip_header=True)          # entity streams are read with skipHeader
    assert r2.version == VERSION and r2.purpose is None
    val = r2.value()
    assert val["decl"].name == "model.entity.ModelEntity" and val["body"] == [1, "x"], val

    # cached-type form: cache declares index 0, the object references it
    t = object_stream(Instance(values=[True, [1, None, 2]], idx=0))
    assert t == "nT0:tai1ni2hg", t
    r3 = Reader(t, skip_header=True)
    r3.types = [ent]
    v3 = r3.value()
    assert v3["type"] == 0 and v3["body"] == [True, [1, None, 2]], v3

    # non-custom class: field list in the decl, declared "z"/"f", values then 'g'
    plain = Decl("appserver.remoting.DataStoreUpdateResponse", base=-1, version=0, custom=False,
                 fields=["updatedEntities", "entityTypeInfo", "returnVal"])
    c2 = type_cache([plain], purpose="login")
    assert c2.startswith("ZVER3y5:loginV" + "y%d:%s" % (len(plain.name), plain.name) + ":i-1:z:f:3:"), c2
    assert c2.endswith("y15:updatedEntitiesy14:entityTypeInfoy9:returnVal:"), c2
    r4 = Reader(c2)
    assert r4.purpose == "login" and r4.version == 3
    t4 = r4.read_typeinfo_loop()
    assert t4[0].fields == ["updatedEntities", "entityTypeInfo", "returnVal"], t4[0].fields

    print("hxinflate self-test OK")
    print("  type cache :", cache)
    print("  entity str :", body)
    print("  cached ref :", t)


if __name__ == "__main__":
    _self_test()
