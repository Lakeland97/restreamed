#!/usr/bin/env python3
"""pakdump.py - minimal Unreal Engine 4 .pak reader (Python 3 stdlib only).

Targets old-era paks (UE 4.11.x, pak version 3) but tolerates the two
common footer layouts found across UE4 generations.

Subcommands:
  pakdump.py <pak> info
  pakdump.py <pak> list [--filter SUBSTR]
  pakdump.py <pak> extract <path-or-substring> --out DIR

Format discovered empirically for hns-WindowsNoEditor.pak (UE 4.11.2):

  Footer (last 44 bytes of file):
      u32 magic          0x5A6F12E1 (bytes e1 12 6f 5a)
      u32 version        3
      u64 index_offset
      u64 index_size
      u8  index_hash[20]
  (validated by: index_offset + index_size == EOF - 44 exactly)

  Index (raw, NOT zlib-compressed, NOT encrypted):
      FString mount_point            ("../../../")
      u32    entry_count
      per entry:
          FString path               (negative length = UTF-16)
          u64 offset                 (points at the on-disk entry header)
          u64 size                   (uncompressed size)
          u64 compressed_size
          u16 compression_method     (0 = none, 1 = zlib)
          u16 flags                  (bit0 = encrypted)   [always 0 here]
          u8   hash[20]
          u8   reserved[5]           (all zero in this pak)
          ---- 53-byte tail, fixed ----

  File data: each entry's serialized 53-byte header (same tail, no
  filename) precedes its bytes at entry.offset; the payload starts at
  entry.offset + 53. All 10,291 entries are stored uncompressed.

  Accounting closes exactly:
      sum(size) + 10291*53 + index_size + 44 == file_size
"""

import argparse
import os
import struct
import sys
import zlib

PAK_MAGIC = 0x5A6F12E1
PAK_MAGIC_BYTES_LE = struct.pack("<I", PAK_MAGIC)  # e1 12 6f 5a
ENTRY_TAIL = 53          # sizes(24) + method(2) + flags(2) + hash(20) + pad(5)
METHOD_NAMES = {0: "none", 1: "zlib", 2: "gzip", 3: "custom"}


class PakError(Exception):
    pass


def method_name(m):
    return METHOD_NAMES.get(m, "unknown(%d)" % m)


def read_fstring(buf, pos):
    """UE FString: i32 length; negative = UTF-16 (length is char count * -1)."""
    if pos + 4 > len(buf):
        raise PakError("FString length past end of buffer")
    (n,) = struct.unpack_from("<i", buf, pos)
    pos += 4
    if n == 0:
        return "", pos
    if n < 0:
        nbytes = -n * 2
        if pos + nbytes > len(buf):
            raise PakError("UTF-16 FString past end of buffer")
        s = buf[pos:pos + nbytes].decode("utf-16-le").rstrip("\0")
        return s, pos + nbytes
    if pos + n > len(buf):
        raise PakError("ANSI FString past end of buffer")
    s = buf[pos:pos + n].decode("latin-1").rstrip("\0")
    return s, pos + n


class Pak:
    def __init__(self, path):
        self.path = path
        self.f = open(path, "rb")  # read-only; never written
        self.f.seek(0, os.SEEK_END)
        self.file_size = self.f.tell()
        self._parse_footer()
        self._load_index()

    # ---------------- footer ----------------
    def _parse_footer(self):
        tail_len = min(64, self.file_size)
        self.f.seek(-tail_len, 2)
        tail = self.f.read(tail_len)

        magic_at = tail.find(PAK_MAGIC_BYTES_LE)
        if magic_at < 0:
            raise PakError("pak magic 0x5A6F12E1 not found in last %d bytes "
                           "(bytes: %s)" % (tail_len, tail.hex()))

        # Layout A (this pak, v3-era): [magic][version][index_off][index_size][hash20]
        base = self.file_size - tail_len + magic_at
        if magic_at + 44 <= len(tail):
            version, ioff, isize = struct.unpack_from("<IQQ", tail, magic_at + 4)
            if ioff + isize == base and ioff >= 0 and isize > 0 and ioff < self.file_size:
                self.version = version
                self.index_offset = ioff
                self.index_size = isize
                self.index_hash = tail[magic_at + 24:magic_at + 44]
                self.footer_layout = "A: [magic u32][version u32][index_offset u64][index_size u64][hash 20B]"
                self.footer_start = base
                self.index_encrypted = False  # version 3 predates index encryption (v4)
                return

        # Layout B (older hint): [index_off u64][index_size u64][hash 20B][magic u32]
        base_b = self.file_size - 40
        if base_b >= 0:
            self.f.seek(base_b, 0)
            raw = self.f.read(40)
            ioff, isize = struct.unpack_from("<QQ", raw, 0)
            if raw[36:40] == PAK_MAGIC_BYTES_LE and ioff + isize == base_b:
                self.version = -1  # unknown for this layout
                self.index_offset = ioff
                self.index_size = isize
                self.index_hash = raw[16:36]
                self.footer_layout = "B: [index_offset u64][index_size u64][hash 20B][magic u32]"
                self.footer_start = base_b
                # A separate index-encryption flag may exist in newer tails;
                # detected when the index fails to parse as text.
                self.index_encrypted = None
                return

        raise PakError("could not validate pak footer layout near magic at "
                       "file offset %d" % base)

    # ---------------- index ----------------
    def _load_index(self):
        if self.index_offset + self.index_size > self.file_size:
            raise PakError("index range [%d, %d) exceeds file size %d"
                           % (self.index_offset,
                              self.index_offset + self.index_size,
                              self.file_size))
        self.f.seek(self.index_offset, 0)
        blob = self.f.read(self.index_size)

        data = None
        # 1) raw (v3-era observed here)
        if self._looks_like_index(blob):
            data = blob
        else:
            # 2) whole-block zlib
            try:
                dec = zlib.decompress(blob)
                if self._looks_like_index(dec):
                    data = dec
            except zlib.error:
                pass
            # 3) zlib with trailing bytes
            if data is None:
                try:
                    d = zlib.decompressobj()
                    dec = d.decompress(blob)
                    if self._looks_like_index(dec):
                        data = dec
                except zlib.error:
                    pass

        if data is None:
            raise PakError("index at offset %d does not parse (raw or zlib) "
                           "and entry paths are not plaintext -> encrypted? "
                           "AES key needed" % self.index_offset)

        if self.index_encrypted is None:
            self.index_encrypted = False

        self._parse_index(data)

    @staticmethod
    def _looks_like_index(buf):
        if len(buf) < 8:
            return False
        (n,) = struct.unpack_from("<i", buf, 0)
        if 0 < n <= 4096 and 4 + n <= len(buf):
            sample = buf[4:4 + min(n, 64)]
            text = sum(1 for b in sample if 32 <= b < 127)
            if text >= max(1, len(sample) * 3 // 4):
                return True
        return False

    def _parse_index(self, buf):
        pos = 0
        self.mount_point, pos = read_fstring(buf, pos)
        if pos + 4 > len(buf):
            raise PakError("missing entry count")
        (self.entry_count,) = struct.unpack_from("<I", buf, pos)
        pos += 4

        entries = []
        encrypted = 0
        methods = {}
        for i in range(self.entry_count):
            path, pos = read_fstring(buf, pos)
            if pos + ENTRY_TAIL > len(buf):
                raise PakError("entry %d tail past end of index "
                               "(paths parsed plausibly up to here)" % i)
            tail = buf[pos:pos + ENTRY_TAIL]
            offset, size, csize = struct.unpack_from("<QQQ", tail, 0)
            (method,) = struct.unpack_from("<H", tail, 24)
            (flags,) = struct.unpack_from("<H", tail, 26)
            if flags & 1:
                encrypted += 1
            methods[method] = methods.get(method, 0) + 1
            if i and (not path or len(path) > 4096 or
                      not all(32 <= ord(c) < 127 for c in path)):
                raise PakError("entry %d path does not look like a path: %r "
                               "-- index layout mismatch" % (i, path[:80]))
            entries.append({
                "path": path,
                "offset": offset,
                "size": size,
                "csize": csize,
                "method": method,
                "flags": flags,
            })
            pos += ENTRY_TAIL

        if pos != len(buf):
            raise PakError("index parsed %d bytes, expected %d -- entry "
                           "struct mismatch" % (pos, len(buf)))

        self.entries = entries
        self.methods = methods
        self.encrypted_entries = encrypted

    # ---------------- entry data ----------------
    def read_entry(self, e):
        if e["flags"] & 1:
            raise PakError("%s: encrypted - AES key needed" % e["path"])
        # payload follows the on-disk 53-byte FPakEntry header
        start = e["offset"] + ENTRY_TAIL
        self.f.seek(start, 0)
        if e["method"] == 0:
            raw = self.f.read(e["size"])
            if len(raw) != e["size"]:
                raise PakError("%s: short read" % e["path"])
            return raw
        raw = self.f.read(e["csize"])
        if len(raw) != e["csize"]:
            raise PakError("%s: short read" % e["path"])
        if e["method"] == 1:
            try:
                return zlib.decompress(raw)  # single-block fallback
            except zlib.error as err:
                raise PakError("%s: zlib decompress failed (%s); multi-block "
                               "layout not supported by this tool"
                               % (e["path"], err))
        raise PakError("%s: unsupported compression method %d"
                       % (e["path"], e["method"]))

    def close(self):
        self.f.close()


# ---------------- commands ----------------

def cmd_info(pak, _args):
    comp = ", ".join("%s x%d" % (method_name(m), c)
                     for m, c in sorted(pak.methods.items()))
    print("file:            %s (%d bytes)" % (pak.path, pak.file_size))
    print("magic:           0x5A6F12E1 (footer at file offset %d, layout: %s)"
          % (pak.footer_start, pak.footer_layout))
    print("pak version:     %s" % pak.version)
    print("mount point:     %r" % pak.mount_point)
    print("index:           offset=%d size=%d hash=%s"
          % (pak.index_offset, pak.index_size, pak.index_hash.hex()))
    enc_entries = "yes (%d entries)" % pak.encrypted_entries \
        if pak.encrypted_entries else "no"
    print("encryption:      %s | INDEX encrypted: %s"
          % (enc_entries, "yes" if pak.index_encrypted else "no"))
    print("compression:     %s" % comp)
    print("entry count:     %d" % len(pak.entries))
    return 0


def cmd_list(pak, args):
    needle = args.filter.lower() if args.filter else None
    shown = 0
    for e in pak.entries:
        if needle and needle not in e["path"].lower():
            continue
        print("%s\t%d\t%d\t%s" % (e["path"], e["size"], e["csize"],
                                  method_name(e["method"])))
        shown += 1
    if args.filter:
        sys.stderr.write("%d matching entries\n" % shown)
    return 0


def _safe_out_path(out_dir, entry_path):
    """Map an entry path onto out_dir, neutralising ../ traversal."""
    parts = []
    for seg in entry_path.replace("\\", "/").split("/"):
        if seg in ("", ".", ".."):
            continue
        parts.append(seg)
    if not parts:
        raise PakError("empty entry path")
    dest = os.path.join(out_dir, *parts)
    real_out = os.path.realpath(out_dir)
    real_dest = os.path.realpath(dest)
    if not real_dest.startswith(real_out + os.sep) and real_dest != real_out:
        raise PakError("path escapes output dir: %r" % entry_path)
    return dest


def cmd_extract(pak, args):
    needle = args.target.lower()
    matches = [e for e in pak.entries
               if needle == e["path"].lower() or needle in e["path"].lower()]
    if not matches:
        sys.stderr.write("no entries match %r\n" % args.target)
        return 1
    ok = failed = 0
    for e in matches:
        try:
            data = pak.read_entry(e)
            dest = _safe_out_path(args.out, e["path"])
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as w:
                w.write(data)
            print("extracted %s -> %s (%d bytes)" % (e["path"], dest, len(data)))
            ok += 1
        except PakError as err:
            sys.stderr.write("FAILED %s: %s\n" % (e["path"], err))
            failed += 1
    sys.stderr.write("%d extracted, %d failed\n" % (ok, failed))
    return 0 if failed == 0 else 2


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pakdump.py",
                                 description="Read/extract UE4 .pak files.")
    ap.add_argument("pak", help="path to .pak (opened read-only)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("info", help="footer/index summary")

    p_list = sub.add_parser("list", help="one line per entry: path, size, "
                                         "compressed size, method")
    p_list.add_argument("--filter", help="case-insensitive substring filter")

    p_ext = sub.add_parser("extract", help="extract matching entries")
    p_ext.add_argument("target", help="exact entry path or substring")
    p_ext.add_argument("--out", required=True, help="output directory")

    args = ap.parse_args(argv)

    pak = Pak(args.pak)  # read-only handle
    try:
        if args.cmd == "info":
            return cmd_info(pak, args)
        if args.cmd == "list":
            return cmd_list(pak, args)
        if args.cmd == "extract":
            return cmd_extract(pak, args)
        raise SystemExit(2)
    finally:
        pak.close()


if __name__ == "__main__":
    sys.exit(main())
