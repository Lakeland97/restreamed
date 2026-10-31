#!/usr/bin/env python3
"""In-place, same-length URL patcher for UE4 v3 (uncompressed) pak files.

Layout (verified against hns-WindowsNoEditor.pak by pakdump.py):
  footer 44B at EOF-44: [magic u32 0x5A6F12E1][version u32][index_offset u64][index_size u64][sha1 20B]
  index: [FString mount][u32 count] then per entry:
    [FString path][u64 offset][u64 size][u64 csize][u16 method][u16 flags][sha1 20B][5 zero bytes]
  on-disk per-file header (53B at entry.offset): [u64 offset][u64 size][u64 csize][u16 method][u16 flags][sha1 20B][5 zeros]
  data at offset+53. Compression none (v3), no encryption.

Patches every occurrence of OLD with NEW (MUST be equal byte length), then refreshes:
  - affected entries' sha1 in BOTH the index and the on-disk header,
  - the footer index sha1.
Fully reversible: run again with the arguments swapped.
"""
import hashlib
import struct
import sys

MAGIC = 0x5A6F12E1
FOOTER_LEN = 44
ENTRY_TAIL = 53  # bytes after the path string in the index; also the on-disk header size
HASH_AT = 28  # offset of sha1 within the 53-byte on-disk header / entry tail


def read_fstring(buf, pos):
    (n,) = struct.unpack_from("<i", buf, pos)
    pos += 4
    if n == 0:
        return "", pos
    if n < 0:
        raw = buf[pos:pos - 2 * n]
        pos += -2 * n
        return raw.decode("utf-16le").rstrip("\x00"), pos
    raw = buf[pos:pos + n]
    pos += n
    return raw.decode("latin-1").rstrip("\x00"), pos


def write_fstring_len_only(path):
    b = path.encode("latin-1")
    return struct.pack("<i", len(b)) + b


def main():
    if len(sys.argv) == 2 and sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)
    if len(sys.argv) == 4:
        pak, old, new = sys.argv[1], sys.argv[2].encode(), sys.argv[3].encode()
        if len(old) != len(new):
            print(f"LENGTH MISMATCH old={len(old)} new={len(new)} — same-length only")
            sys.exit(1)
    elif len(sys.argv) == 2:
        pak, old, new = sys.argv[1], b"", b""
    else:
        print(__doc__)
        sys.exit(2)

    with open(pak, "r+b") as f:
        f.seek(0, 2)
        eof = f.tell()
        f.seek(eof - FOOTER_LEN)
        footer = bytearray(f.read(FOOTER_LEN))
        magic, ver, idx_off, idx_size = struct.unpack_from("<IIQQ", footer, 0)
        if magic != MAGIC:
            print(f"bad magic 0x{magic:08x}"); sys.exit(1)
        print(f"version={ver} index@{idx_off} size={idx_size}")

        f.seek(idx_off)
        index = bytearray(f.read(idx_size))

        # parse index
        pos = 0
        mount, pos = read_fstring(index, pos)
        (count,) = struct.unpack_from("<I", index, pos); pos += 4
        entries = []
        for _ in range(count):
            path, p2 = read_fstring(index, pos)
            tail_at = p2  # start of 53-byte tail
            off, size, csize, method, flags = struct.unpack_from("<QQQHH", index, tail_at)
            hash_at = tail_at + HASH_AT
            entries.append({"path": path, "off": off, "size": size,
                            "hash_at": hash_at, "tail_at": tail_at})
            pos = tail_at + ENTRY_TAIL
        if pos != len(index):
            print(f"index parse drift: pos={pos} len={len(index)}"); sys.exit(1)
        print(f"entries={len(entries)} mount={mount!r}")

        # find and apply replacements across whole file
        f.seek(0)
        data = bytearray(f.read())
        hits = []
        if len(sys.argv) == 4:
            start = 0
            while True:
                i = data.find(old, start)
                if i < 0:
                    break
                hits.append(i)
                start = i + len(old)
            if not hits:
                print("OLD string not found in content (already patched?) — running hash self-heal only")
            for i in hits:
                data[i:i + len(old)] = new
        else:
            print("no URL args — hash self-heal mode")
        print(f"occurrences={len(hits)} at {hits}")

        # refresh sha1 in index + on-disk header for EVERY entry whose hash mismatches
        # (self-healing: catches both newly-patched entries and any historical drift).
        # UE4.11 validates per-entry index hashes at read time — a stale one makes the
        # engine REFUSE the file (observed: "Pak file hash does not match its index entry").
        touched = []
        for e in entries:
            payload_lo = e["off"] + ENTRY_TAIL
            payload_hi = payload_lo + e["size"]
            digest = hashlib.sha1(bytes(data[payload_lo:payload_hi])).digest()
            idx_hash = bytes(index[e["hash_at"]:e["hash_at"] + 20])
            disk_hash = bytes(data[e["off"] + HASH_AT:e["off"] + HASH_AT + 20])
            if idx_hash != digest or disk_hash != digest:
                struct.pack_into("<20s", index, e["hash_at"], digest)          # index copy
                struct.pack_into("<20s", data, e["off"] + HASH_AT, digest)     # on-disk header copy
                touched.append(e["path"])
        for p in touched:
            print(f"  refreshed hash: {p}")
        print(f"hash mismatches fixed: {len(touched)}")

        # footer index hash covers index bytes
        struct.pack_into("<20s", footer, FOOTER_LEN - 20, hashlib.sha1(bytes(index)).digest())

        # splice index+footer into the data buffer, then write ONCE — writing data last
        # would clobber the index/footer edits (they were made on separate buffers).
        data[idx_off:idx_off + idx_size] = index
        data[eof - FOOTER_LEN:] = footer
        f.seek(0)
        f.write(data)
        f.flush()
    print(f"done: {len(hits)} replacement(s), {len(touched)} entry hash(es) refreshed, footer re-signed")


if __name__ == "__main__":
    main()
