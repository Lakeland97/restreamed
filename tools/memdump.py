#!/usr/bin/env python3
"""Hexdump arbitrary addresses out of a minidump (using its memory ranges)."""
import struct, sys

def load_ranges(path):
    data = open(path, 'rb').read()
    nstreams, dir_rva = struct.unpack_from('<II', data, 8)
    streams = {}
    for i in range(nstreams):
        stype, size, rva = struct.unpack_from('<III', data, dir_rva + i * 12)
        streams.setdefault(stype, []).append((size, rva))
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
    return data, ranges

def main():
    path = sys.argv[1]
    addr = int(sys.argv[2], 16)
    length = int(sys.argv[3], 16) if len(sys.argv) > 3 else 0x100
    data, ranges = load_ranges(path)
    print('ranges covering:')
    for start, msize, mrva in ranges:
        if start <= addr < start + msize:
            print('  %016x-%016x (file off %x)' % (start, start + msize, mrva))
    # print in chunks
    cur = addr
    end = addr + length
    while cur < end:
        chunk = None
        for start, msize, mrva in ranges:
            if start <= cur < start + msize:
                n = min(end, start + msize) - cur
                o = mrva + (cur - start)
                chunk = data[o:o + n]
                break
        if not chunk:
            print('%016x: <unmapped>' % cur)
            cur += 16
            continue
        for i in range(0, len(chunk), 16):
            blk = chunk[i:i + 16]
            hexs = ' '.join('%02x' % b for b in blk)
            asc = ''.join(chr(b) if 32 <= b < 127 else '.' for b in blk)
            print('%016x: %-47s %s' % (cur + i, hexs, asc))
        cur += len(chunk)

main()
