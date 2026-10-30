#!/usr/bin/env python3
"""Minimal minidump reader: exception record, threads, modules, and a raw
stack scan for return addresses falling inside a target module range."""
import struct, sys

STREAM_THREAD_LIST = 3
STREAM_MODULE_LIST = 4
STREAM_MEMORY_LIST = 5
STREAM_EXCEPTION = 6
STREAM_MEMORY64_LIST = 9

def main(path, scan_lo=None, scan_hi=None):
    data = open(path, 'rb').read()
    if data[:4] != b'MDMP':
        print('not a minidump'); return
    nstreams, dir_rva = struct.unpack_from('<II', data, 8)
    streams = {}
    for i in range(nstreams):
        stype, size, rva = struct.unpack_from('<III', data, dir_rva + i * 12)
        streams.setdefault(stype, []).append((size, rva))

    # modules
    mods = []
    for size, rva in streams.get(STREAM_MODULE_LIST, []):
        n = struct.unpack_from('<I', data, rva)[0]
        for i in range(n):
            off = rva + 4 + i * 108
            base, msize, csum, tds, namerva = struct.unpack_from('<QIIII', data, off)
            nlen = struct.unpack_from('<I', data, namerva)[0]
            name = data[namerva + 4: namerva + 4 + nlen].decode('utf-16le', 'replace')
            mods.append((base, msize, name))
    print('=== modules (%d) ===' % len(mods))
    for base, msize, name in mods:
        print('  %016x-%016x %s' % (base, base + msize, name.split('\\')[-1]))

    # threads
    threads = []
    for size, rva in streams.get(STREAM_THREAD_LIST, []):
        n = struct.unpack_from('<I', data, rva)[0]
        for i in range(n):
            off = rva + 4 + i * 48
            tid, susp, prioc, prio = struct.unpack_from('<IIII', data, off)
            teb, stack_start, stack_size, stack_rva = struct.unpack_from('<QQII', data, off + 16)
            ctx_size, ctx_rva = struct.unpack_from('<II', data, off + 40)
            rsp = struct.unpack_from('<Q', data, ctx_rva + 0x98)[0]
            rip = struct.unpack_from('<Q', data, ctx_rva + 0xF8)[0]
            threads.append(dict(tid=tid, rsp=rsp, rip=rip, stack_start=stack_start,
                                stack_size=stack_size, stack_rva=stack_rva, ctx_rva=ctx_rva))
    print('=== threads (%d) ===' % len(threads))
    for t in threads:
        print('  tid=%d rip=%016x rsp=%016x stack=%016x+%d' %
              (t['tid'], t['rip'], t['rsp'], t['stack_start'], t['stack_size']))

    # exception
    fault_tid = None
    for size, rva in streams.get(STREAM_EXCEPTION, []):
        tid = struct.unpack_from('<I', data, rva)[0]
        er = rva + 8
        code, flags, nested, addr = struct.unpack_from('<IIQQ', data, er)
        nparams = struct.unpack_from('<I', data, er + 24)[0]
        params = struct.unpack_from('<15Q', data, er + 32)
        fault_tid = tid
        print('=== exception ===')
        print('  thread=%d code=%08x flags=%08x addr=%016x nparams=%d' %
              (tid, code, flags, addr, nparams))
        for i in range(min(nparams, 15)):
            print('    param[%d]=%016x' % (i, params[i]))
        # string at param pointers, if any
        for i in range(min(nparams, 15)):
            p = params[i]
            if p and p < len(data) - 64:
                pass
        stack_ctx = struct.unpack_from('<I', data, er + 32 + 15 * 8)[0]
        tc_rva = struct.unpack_from('<I', data, er + 36 + 15 * 8)[0]
        frsp = struct.unpack_from('<Q', data, tc_rva + 0x98)[0]
        frip = struct.unpack_from('<Q', data, tc_rva + 0xF8)[0]
        print('  fault ctx: rip=%016x rsp=%016x' % (frip, frsp))

    # stack scan for the faulting thread
    if scan_lo is not None and fault_tid is not None:
        t = [x for x in threads if x['tid'] == fault_tid]
        if t:
            t = t[0]
            lo, hi = scan_lo, scan_hi
            print('=== stack scan tid=%d for code ptrs in %016x-%016x ===' % (fault_tid, lo, hi))
            # gather memory ranges from MemoryList (32-bit descriptors against 64-bit addresses not
            # supported) or Memory64List
            ranges = []
            for size, rva in streams.get(STREAM_MEMORY64_LIST, []):
                nranges, base_rva = struct.unpack_from('<QQ', data, rva)
                off = rva + 16
                cur = base_rva
                for i in range(nranges):
                    start, msize = struct.unpack_from('<QQ', data, off + i * 16)
                    ranges.append((start, msize, cur))
                    cur += msize
            for size, rva in streams.get(STREAM_MEMORY_LIST, []):
                n = struct.unpack_from('<I', data, rva)[0]
                for i in range(n):
                    start, msize, mrva = struct.unpack_from('<QII', data, rva + 4 + i * 16)
                    ranges.append((start, msize, mrva))
            print('  memory ranges: %d' % len(ranges))

            def read_mem(addr, size):
                for start, msize, mrva in ranges:
                    if start <= addr and addr + size <= start + msize:
                        o = mrva + (addr - start)
                        return data[o:o + size]
                return None

            # prefer the thread's own stack record, then fall back to memory ranges
            if t['stack_rva'] and t['stack_size']:
                rsp = t['rsp']
                if not (t['stack_start'] <= rsp < t['stack_start'] + t['stack_size']):
                    rsp = t['stack_start']
                raw = data[t['stack_rva']:t['stack_rva'] + t['stack_size']]
                base = t['stack_start']
                for i in range(0, len(raw) - 8, 8):
                    v = struct.unpack_from('<Q', raw, i)[0]
                    if lo <= v < hi:
                        print('    [%016x] %016x  (base+0x%x)' % (base + i, v, v - lo))
            else:
                print('  no stack record for tid')
    else:
        # raw dump of the faulting thread's stack values from Memory64List ranges
        pass

if __name__ == '__main__':
    args = sys.argv[1:]
    path = args[0]
    lo = int(args[1], 16) if len(args) > 1 else None
    hi = int(args[2], 16) if len(args) > 2 else None
    main(path, lo, hi)
