#!/usr/bin/env python3
"""Resolve a captured exception against an in-guest minidump extraction.

Inputs (produced in-guest by tools/dumpinfo5.ps1, pulled to runtime/):
  cf9_mods.txt  - "<base hex16> <size hex> <path>" per module
  cf9_exc.txt   - exception record + context + stack window descriptor
  cf9_stack.bin - the live stack window bytes
  cf9_ctx.bin   - the 1232-byte CONTEXT

Outputs the exception, the faulting address resolved to module+offset, and the return
addresses found in the stack window (stack order from RSP), so a fatal AV can be traced
back to the calling code. Streamline.exe offsets are also printed as fileVAs
(fileVA = 0x140000000 + runtime_offset) ready for Ghidra.
"""
import bisect
import collections
import re
import struct
import sys

IMAGE_BASE = 0x140000000


def load(path, default=None):
    try:
        return open(path, "r", errors="replace").read()
    except OSError:
        return default


def parse_mods(txt):
    mods = []
    for ln in (txt or "").splitlines():
        m = re.match(r"^([0-9a-f]{16}) ([0-9a-f]+) (.+)$", ln.strip())
        if m:
            mods.append((int(m.group(1), 16), int(m.group(2), 16), m.group(3)))
    return sorted(mods)


class Resolver:
    def __init__(self, mods):
        self.mods = mods
        self.bases = [m[0] for m in mods]

    def __call__(self, v):
        i = bisect.bisect_right(self.bases, v) - 1
        if i < 0:
            return None
        b, s, n = self.mods[i]
        if b <= v < b + s:
            return (n.split("\\")[-1], v - b, n)
        return None

    def label(self, v, exe_base=None):
        r = self(v)
        if not r:
            return f"0x{v:016x} (unmapped)"
        name, off, full = r
        s = f"{name}+0x{off:x}"
        if name.lower() == "streamline.exe":
            s += f"  [fileVA 0x{IMAGE_BASE + off:x}]"
            if exe_base:
                s += f"  [rva 0x{IMAGE_BASE + off - IMAGE_BASE:x}]"
        return s


def parse_exc(txt):
    d = {}
    parts = {}
    for ln in (txt or "").splitlines():
        ln = ln.strip()
        if ln.startswith("stack start="):
            d["stack"] = ln
        elif ln.startswith("window "):
            parts["window"] = ln
        elif "=" in ln:
            k, _, v = ln.partition("=")
            d[k.strip()] = v.strip()
    d.update(parts)
    return d


def main():
    d = sys.argv[1] if len(sys.argv) > 1 else "runtime"
    mods = parse_mods(load(f"{d}/v5_cf9_mods.txt") or load(f"{d}/v_cf9_mods.txt"))
    exc = parse_exc(load(f"{d}/v5_cf9_exc.txt") or load(f"{d}/v_cf9_exc.txt"))
    try:
        stack = open(f"{d}/cf9b_stack.bin", "rb").read()
    except OSError:
        try:
            stack = open(f"{d}/cf9_stack.bin", "rb").read()
        except OSError:
            stack = b""

    res = Resolver(mods)
    exe_base = next((m[0] for m in mods if m[2].lower().endswith("streamline.exe")), None)
    print(f"modules: {len(mods)}   exe_base: {exe_base and hex(exe_base)}")
    code = exc.get("code")
    print(f"EXCEPTION code={code} flags={exc.get('flags')} tid={exc.get('tid')} "
          f"addr={exc.get('addr')}")
    if exc.get("addr"):
        print(f"  faulting RIP -> {res.label(int(exc['addr'], 16), exe_base)}")
    npar = int(exc.get("nparams", "0") or 0)
    params = [int(exc[f"param[{i}]"], 16) for i in range(npar) if f"param[{i}]" in exc]
    names = {0xC0000005: ["access-type (0=read,1=write,8=DEP)", "accessed address"]}
    taglist = names.get(int(code, 16), []) if code else []
    for i, p in enumerate(params):
        tag = taglist[i] if i < len(taglist) else None
        print(f"  param[{i}] = {p:#018x}"
              + (f"  <- {tag}" if tag else "")
              + (f"  -> {res.label(p, exe_base)}" if res(p) else ""))
    rip = exc.get("rip")
    rsp = int(exc["rsp"], 16) if exc.get("rsp") else None
    rbp = int(exc["rbp"], 16) if exc.get("rbp") else None
    if rip:
        print(f"CONTEXT rip={rip} -> {res.label(int(rip, 16), exe_base)}")
    if rbp:
        print(f"        rbp={rbp:#x} -> {res.label(rbp, exe_base)}")
    win = re.search(r"window start=([0-9a-f]+) len=([0-9a-f]+)", exc.get("window", ""))
    win_start = int(win.group(1), 16) if win else None
    if win_start is not None:
        print(f"STACK window start={win_start:#x} len={len(stack):#x} "
              f"(rsp inside={bool(rsp and win_start <= rsp < win_start + len(stack))})")
    if not stack:
        print("no stack bytes")
        return
    off = 0
    if rsp and win_start is not None and win_start <= rsp < win_start + len(stack):
        off = rsp - win_start
    print(f"--- code pointers on the stack window (from RSP, offset {off:#x}) ---")
    seq = []
    for j in range(off, len(stack) - 8, 8):
        v = struct.unpack_from("<Q", stack, j)[0]
        r = res(v)
        if r:
            seq.append((j, r[0], r[1]))
    seen = collections.OrderedDict()
    for j, name, o in seq:
        seen.setdefault((name, o), j)
    for (name, o), j in list(seen.items())[:60]:
        lbl = f"{name}+0x{o:x}"
        if name.lower() == "streamline.exe":
            lbl += f"  [fileVA 0x{IMAGE_BASE + o:x}]"
        print(f"  {lbl}   (window+{j:#x}, rsp+{j - off:#x})")
    print("--- module aggregate ---")
    for nm, c in collections.Counter(x[1] for x in seq).most_common(12):
        print(f"  {nm}: {c}")


if __name__ == "__main__":
    main()
