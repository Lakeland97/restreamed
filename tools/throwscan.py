#!/usr/bin/env python3
"""From a minidump, for a C++ throw (0xE06D7363): walk ThrowInfo -> TypeDescriptor
name, and scan the faulting thread's stack for return addresses inside the main
module, printing them as file VAs (image base assumed 0x140000000)."""
import struct, sys

IMAGE_BASE = 0x140000000

def load(path):
    data = open(path, 'rb').read()
    nstreams, dir_rva = struct.unpack_from('<II', data, 8)
    streams = {}
    for i in range(nstreams):
        stype, size, rva = struct.unpack_from('<III', data, dir_rva + i * 12)
        streams.setdefault(stype, []).append((size, rva))
    mods = []
    for size, rva in streams.get(4, []):
        n = struct.unpack_from('<I', data, rva)[0]
        for i in range(n):
            off = rva + 4 + i * 108
            base, msize, csum, tds, namerva = struct.unpack_from('<QIIII', data, off)
            nlen = struct.unpack_from('<I', data, namerva)[0]
            name = data[namerva + 4: namerva + 4 + nlen].decode('utf-16le', 'replace')
            mods.append((base, msize, name.split('\\')[-1]))
    threads = []
    for size, rva in streams.get(3, []):
        n = struct.unpack_from('<I', data, rva)[0]
        for i in range(n):
            off = rva + 4 + i * 48
            tid = struct.unpack_from('<I', data, off)[0]
            teb, sstart, ssize, srva = struct.unpack_from('<QQII', data, off + 16)
            csize, crva = struct.unpack_from('<II', data, off + 40)
            rsp = struct.unpack_from('<Q', data, crva + 0x98)[0]
            rip = struct.unpack_from('<Q', data, crva + 0xF8)[0]
            threads.append(dict(tid=tid, rsp=rsp, rip=rip, sstart=sstart, ssize=ssize, srva=srva))
    exc = None
    for size, rva in streams.get(6, []):
        tid = struct.unpack_from('<I', data, rva)[0]
        er = rva + 8
        code, flags, nested, addr = struct.unpack_from('<IIQQ', data, er)
        nparams = struct.unpack_from('<I', data, er + 24)[0]
        params = struct.unpack_from('<15Q', data, er + 32)
        exc = dict(tid=tid, code=code, addr=addr, nparams=nparams, params=params)
    ranges = []
    for size, rva in streams.get(9, []):
        nranges, base_rva = struct.unpack_from('<QQ', data, rva)
        off = rva + 16
        cur = base_rva
        for i in range(nranges):
            start, msize = struct.unpack_from('<QQ', data, off + i * 16)
            ranges.append((start, msize, cur))
            cur += msize
    for size, rva in streams.get(5, []):
        n = struct.unpack_from('<I', data, rva)[0]
        for i in range(n):
            start, msize, mrva = struct.unpack_from('<QII', data, rva + 4 + i * 16)
            ranges.append((start, msize, mrva))
    return data, mods, threads, exc, ranges

def mk_reader(data, ranges):
    def rd(addr, size):
        for start, msize, mrva in ranges:
            if start <= addr and addr + size <= start + msize:
                o = mrva + (addr - start)
                return data[o:o + size]
        return None
    return rd

def rva_to_file(rva, image_base):
    return image_base + (rva - 0x140000000)

def main():
    path = sys.argv[1]
    data, mods, threads, exc, ranges = load(path)
    rd = mk_reader(data, ranges)
    tgt_base, tgt_size = None, None
    for base, msize, name in mods:
        if name.lower().startswith('streamline'):
            tgt_base, tgt_size = base, msize
    print('exe base=%016x size=%x' % (tgt_base, tgt_size))
    print('exception code=%08x tid=%d addr=%016x params=%d' %
          (exc['code'], exc['tid'], exc['addr'], exc['nparams']))
    # ThrowInfo walk for C++ throws
    if exc['code'] == 0xE06D7363 and exc['nparams'] >= 4:
        obj_ptr = exc['params'][1]
        ti_ptr = exc['params'][2]
        print('thrown object ptr=%016x ThrowInfo ptr=%016x' % (obj_ptr, ti_ptr))
        ti = rd(ti_ptr, 16)
        if ti:
            attrs, unwind, fwd, cta_rva = struct.unpack_from('<IIII', ti, 0)
            print('ThrowInfo: attrs=%08x pUnwind=%08x pForward=%08x CatchableTypeArrayRva=%08x' %
                  (attrs, unwind, fwd, cta_rva))
            if cta_rva:
                cta = rd(rva_to_file(cta_rva, tgt_base), 8 + 4 * 8)
                if cta:
                    count = struct.unpack_from('<I', cta, 0)[0]
                    print('CatchableTypeArray count=%d' % count)
                    for i in range(min(count, 8)):
                        ct_rva = struct.unpack_from('<I', cta, 4 + i * 4)[0]
                        ct = rd(rva_to_file(ct_rva, tgt_base), 8)
                        if not ct:
                            print('  ct[%d] rva=%08x unreadable' % (i, ct_rva)); continue
                        props, pt_rva = struct.unpack_from('<II', ct, 0)
                        td = rd(rva_to_file(pt_rva, tgt_base), 64 + 256)
                        name = '?'
                        if td:
                            raw = td[16:]
                            z = raw.split(b'\x00')[0]
                            name = z.decode('ascii', 'replace')
                        print('  ct[%d] props=%08x typeDescRva=%08x type=%s' % (i, props, pt_rva, name))
        obj = rd(obj_ptr, 32)
        if obj:
            print('thrown object first 32 bytes:', obj.hex())
    # stack scan of faulting thread
    t = [x for x in threads if x['tid'] == exc['tid']]
    if not t:
        print('faulting thread record missing'); return
    t = t[0]
    print('faulting thread rsp=%016x rip=%016x stack=%016x+%x' % (t['rsp'], t['rip'], t['sstart'], t['ssize']))
    if t['srva'] and t['ssize']:
        base = t['sstart']
        raw = data[t['srva']:t['srva'] + t['ssize']]
        print('--- stack code pointers in exe (rsp upward) ---')
        start_idx = 0
        if base <= t['rsp'] < base + len(raw):
            start_idx = t['rsp'] - base
        for i in range(start_idx // 8 * 8, len(raw) - 8, 8):
            v = struct.unpack_from('<Q', raw, i)[0]
            if tgt_base <= v < tgt_base + tgt_size:
                fva = IMAGE_BASE + (v - tgt_base)
                print('  [%016x] %016x  fileVA=%012x' % (base + i, v, fva))

main()
