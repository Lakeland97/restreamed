#!/usr/bin/env python3
"""Recover the frame chain of the faulting thread of a Windows x64 minidump.

The minidump (cd-9.dmp) was written without module images, so system DLLs
(kernel32/ntdll/KERNELBASE/...) cannot be unwound from the dump.  The main
module (Streamline.exe) *is* available on disk, so its PE .pdata/.xdata unwind
tables are parsed here and used to unwind every frame that lies inside
Streamline.exe.  Frames outside the module are stepped with a validated
leaf/resync rule (advance 8 bytes until a value that points into executable
module memory), which is accurate for the transparent RaiseException stubs in
this dump.

For each exe frame the script also reports the stack slot it reads the return
address from (rsp + total stack adjustment) and then checks every exe frame
transition without a disassembler:

  * the return address must be a `call rel32` return (byte 0xE8 five bytes
    below the address) inside the caller function, and
  * that call must target the frame's own function -- either directly, or
    through one of the chained (UNW_FLAG_CHAININFO) .pdata chunks of it.

A transition that fails the second test means a frame was skipped: the static
callee tore its frame down and jumped into the frame that is on the stack
(tail call), which is exactly what happens between FUN_140254250 and
FUN_14133249f in this dump (FUN_141320070 tail-jumped via `jmp [rax+0x60]`).

Standard library only.  Read-only on the dump and the exe.
"""
import os
import struct
import sys

EXE_PATH = ('/Users/user/Scripts/git/restreamed-dev/game/hns/Binaries/Win64/'
            'Streamline.exe')
DMP_PATH = '/Users/user/Scripts/git/restreamed-dev/runtime/cd-9.dmp'
OUT_PATH = ('/Users/user/Scripts/git/restreamed-dev/decomp/ghidra_out/'
            'R785_frames_cd9.txt')

IMAGE_BASE = 0x140000000      # preferred image base of Streamline.exe
MAX_FRAMES = 40
SCAN_LIMIT = 0x8000           # how far a module-less frame may be resynced

IMAGE_SCN_MEM_EXECUTE = 0x20000000

# UNWIND_INFO flags
UNW_FLAG_CHAININFO = 0x4

# unwind operations
UWOP_PUSH_NONVOL = 0
UWOP_ALLOC_LARGE = 1
UWOP_ALLOC_SMALL = 2
UWOP_SET_FPREG = 3
UWOP_SAVE_NONVOL = 4
UWOP_SAVE_NONVOL_FAR = 5
UWOP_EPILOG = 6
UWOP_SPARE = 7
UWOP_SAVE_XMM128 = 8
UWOP_SAVE_XMM128_FAR = 9
UWOP_PUSH_MACHFRAME = 10


def u16(b, o):
    return struct.unpack_from('<H', b, o)[0]


def u32(b, o):
    return struct.unpack_from('<I', b, o)[0]


def u64(b, o):
    return struct.unpack_from('<Q', b, o)[0]


# --------------------------------------------------------------------------
# PE parsing: sections, RUNTIME_FUNCTION[] (.pdata), UNWIND_INFO (.xdata)
# --------------------------------------------------------------------------
class PE(object):
    def __init__(self, path):
        self.data = open(path, 'rb').read()
        d = self.data
        pe = u32(d, 0x3c)
        if d[pe:pe + 4] != b'PE\0\0':
            raise ValueError('not a PE file')
        self.machine = u16(d, pe + 4)
        self.nsec = u16(d, pe + 6)
        optsize = u16(d, pe + 20)
        opt = pe + 24
        self.magic = u16(d, opt)
        self.image_base = u64(d, opt + 24)
        dd = opt + 112                       # data directories (PE32+)
        self.exception_rva = u32(d, dd + 3 * 8)
        self.exception_size = u32(d, dd + 3 * 8 + 4)

        sec = opt + optsize
        self.sections = []
        for i in range(self.nsec):
            o = sec + i * 40
            self.sections.append(dict(
                name=d[o:o + 8].rstrip(b'\0').decode('latin1'),
                vsize=u32(d, o + 8),
                va=u32(d, o + 12),
                rawsize=u32(d, o + 16),
                rawptr=u32(d, o + 20),
                chars=u32(d, o + 36)))
        self._build_runtime_functions()

    def rva_to_off(self, rva):
        for s in self.sections:
            if s['va'] <= rva < s['va'] + max(s['vsize'], s['rawsize']):
                return s['rawptr'] + (rva - s['va'])
        return None

    def read_rva(self, rva, size):
        o = self.rva_to_off(rva)
        if o is None:
            return None
        return self.data[o:o + size]

    def is_exec_rva(self, rva):
        for s in self.sections:
            if (s['chars'] & IMAGE_SCN_MEM_EXECUTE and
                    s['va'] <= rva < s['va'] + max(s['vsize'], s['rawsize'])):
                return True
        return False

    def _build_runtime_functions(self):
        raw = self.read_rva(self.exception_rva, self.exception_size)
        n = self.exception_size // 12
        self.rf = []
        for i in range(n):
            b, e, u = struct.unpack_from('<III', raw, i * 12)
            if b or e or u:
                self.rf.append((b, e, u))
        self.rf.sort()
        self.rf_map = dict((b, (b, e, u)) for b, e, u in self.rf)

    def lookup_function(self, rva, use_prev=True):
        """RUNTIME_FUNCTION for a code address.

        For a return address (the address *after* a call) the byte before it is
        guaranteed to lie inside the caller, so use_prev probes rva-1 first --
        this attributes a tail-position `call` to the function that ends
        exactly at rva.
        """
        probes = ([rva - 1, rva] if (use_prev and rva > 0) else [rva])
        for p in probes:
            lo, hi = 0, len(self.rf) - 1
            while lo <= hi:
                mid = (lo + hi) // 2
                b, e, u = self.rf[mid]
                if p < b:
                    hi = mid - 1
                elif p >= e:
                    lo = mid + 1
                else:
                    return (b, e, u)
        return None

    def unwind_info(self, rva):
        raw = self.read_rva(rva, 4)
        if raw is None or len(raw) < 4:
            return None
        flags = (raw[0] >> 3) & 0x1f
        count = raw[2]
        codes_raw = self.read_rva(rva + 4, count * 2)
        codes = []
        if codes_raw is not None:
            for i in range(count):
                codes.append((codes_raw[i * 2],
                              codes_raw[i * 2 + 1] & 0xF,
                              (codes_raw[i * 2 + 1] >> 4) & 0xF))
        chain = None
        if flags & UNW_FLAG_CHAININFO:
            crva = (rva + 4 + 2 * count + 3) & ~3
            craw = self.read_rva(crva, 12)
            if craw is not None and len(craw) == 12:
                chain = struct.unpack('<III', craw)
        return dict(version=raw[0] & 0x7, flags=flags, prolog=raw[1],
                    count=count, frame_reg=raw[3] & 0xF,
                    frame_off=(raw[3] >> 4) & 0xF, codes=codes, chain=chain)

    @staticmethod
    def _codes_adjustment(codes):
        """Total rsp movement of the unwind codes (pushes + allocations)."""
        adj = 0
        i = 0
        while i < len(codes):
            _, op, info = codes[i]
            if op == UWOP_PUSH_NONVOL:
                adj += 8
            elif op == UWOP_ALLOC_LARGE:
                if info == 0:
                    adj += codes[i + 1][0] * 8
                    i += 1
                else:
                    adj += codes[i + 1][0] | (codes[i + 2][0] << 16)
                    i += 2
            elif op == UWOP_ALLOC_SMALL:
                adj += (info + 1) * 8
            elif op == UWOP_SET_FPREG:
                pass
            elif op == UWOP_SAVE_NONVOL:
                i += 1
            elif op == UWOP_SAVE_NONVOL_FAR:
                i += 2
            elif op == UWOP_SAVE_XMM128:
                i += 1
            elif op == UWOP_SAVE_XMM128_FAR:
                i += 2
            elif op == UWOP_PUSH_MACHFRAME:
                adj += 0x30 if info else 0x28
            i += 1
        return adj

    def _adjust_chain(self, uinfo_rva, seen):
        total = 0
        while uinfo_rva is not None and uinfo_rva not in seen:
            seen.add(uinfo_rva)
            ui = self.unwind_info(uinfo_rva)
            if ui is None:
                break
            total += self._codes_adjustment(ui['codes'])
            uinfo_rva = ui['chain'][2] if ui['chain'] else None
        return total

    def stack_adjustment(self, rva, use_prev=True):
        rf = self.lookup_function(rva, use_prev)
        if rf is None:
            return 0
        return self._adjust_chain(rf[2], set())

    def chain_begins(self, begin_rva):
        """This .pdata entry's BeginAddress plus every CHAININFO parent's."""
        out, seen, cur = [], set(), begin_rva
        while cur is not None and cur not in seen and cur in self.rf_map:
            seen.add(cur)
            out.append(cur)
            ui = self.unwind_info(self.rf_map[cur][2])
            if not ui or not ui['chain']:
                break
            cur = ui['chain'][0]
        return out

    def call_target(self, return_rva):
        """Target of a plain `call rel32` whose return address is return_rva.

        Returns the target RVA, or None when the byte five positions below the
        return address is not 0xE8 (an indirect call, or not a call at all).
        """
        raw = self.read_rva(return_rva - 5, 5)
        if raw is None or raw[0] != 0xE8:
            return None
        rel = struct.unpack_from('<i', raw, 1)[0]
        return return_rva + rel


# --------------------------------------------------------------------------
# Minidump parsing (mirrors tools/mdread.py + tools/throwscan.py)
# --------------------------------------------------------------------------
class Dump(object):
    def __init__(self, path):
        self.data = open(path, 'rb').read()
        d = self.data
        nstreams, dir_rva = struct.unpack_from('<II', d, 8)
        self.streams = {}
        for i in range(nstreams):
            st, size, rva = struct.unpack_from('<III', d, dir_rva + i * 12)
            self.streams.setdefault(st, []).append((size, rva))
        self._mods()
        self._ranges()
        self._threads()
        self._exception()

    def _mods(self):
        self.mods = []
        for size, rva in self.streams.get(4, []):
            n = u32(self.data, rva)
            for i in range(n):
                off = rva + 4 + i * 108
                base, msize, _, _, namerva = struct.unpack_from(
                    '<QIIII', self.data, off)
                nlen = u32(self.data, namerva)
                name = self.data[namerva + 4:namerva + 4 + nlen].decode(
                    'utf-16le', 'replace')
                self.mods.append((base, msize, name.split('\\')[-1]))

    def _ranges(self):
        self.ranges = []
        for size, rva in self.streams.get(9, []):
            nranges, base_rva = struct.unpack_from('<QQ', self.data, rva)
            off = rva + 16
            cur = base_rva
            for i in range(nranges):
                start, msize = struct.unpack_from('<QQ', self.data,
                                                  off + i * 16)
                self.ranges.append((start, msize, cur))
                cur += msize
        for size, rva in self.streams.get(5, []):
            n = u32(self.data, rva)
            for i in range(n):
                start, msize, mrva = struct.unpack_from(
                    '<QII', self.data, rva + 4 + i * 16)
                self.ranges.append((start, msize, mrva))

    def _threads(self):
        self.threads = []
        for size, rva in self.streams.get(3, []):
            n = u32(self.data, rva)
            for i in range(n):
                off = rva + 4 + i * 48
                tid = u32(self.data, off)
                teb, sstart, ssize, srva = struct.unpack_from(
                    '<QQII', self.data, off + 16)
                _, crva = struct.unpack_from('<II', self.data, off + 40)
                self.threads.append(dict(
                    tid=tid, teb=teb, sstart=sstart, ssize=ssize, srva=srva,
                    crva=crva, rsp=u64(self.data, crva + 0x98),
                    rip=u64(self.data, crva + 0xF8)))

    def _exception(self):
        self.exc = None
        for size, rva in self.streams.get(6, []):
            tid = u32(self.data, rva)
            er = rva + 8
            code, _, _, addr = struct.unpack_from('<IIQQ', self.data, er)
            nparams = u32(self.data, er + 24)
            params = struct.unpack_from('<15Q', self.data, er + 32)
            tcrva = u32(self.data, rva + 8 + 152 + 4)
            self.exc = dict(tid=tid, code=code, addr=addr, nparams=nparams,
                            params=params, tcrva=tcrva,
                            rsp=u64(self.data, tcrva + 0x98),
                            rip=u64(self.data, tcrva + 0xF8))

    def read(self, addr, size):
        for start, msize, mrva in self.ranges:
            if start <= addr and addr + size <= start + msize:
                o = mrva + (addr - start)
                return self.data[o:o + size]
        t = self.fault_thread()
        if t and t['srva'] and t['ssize']:
            if t['sstart'] <= addr and addr + size <= t['sstart'] + t['ssize']:
                o = t['srva'] + (addr - t['sstart'])
                return self.data[o:o + size]
        return None

    def read_q(self, addr):
        raw = self.read(addr, 8)
        return struct.unpack('<Q', raw)[0] if raw and len(raw) == 8 else None

    def fault_thread(self):
        if not self.exc:
            return None
        for t in self.threads:
            if t['tid'] == self.exc['tid']:
                return t
        return None

    def module_of(self, va):
        for base, msize, name in self.mods:
            if base <= va < base + msize:
                return (name, base, msize)
        return None


# --------------------------------------------------------------------------
# unwinding
# --------------------------------------------------------------------------
def main():
    pe = PE(EXE_PATH)
    dump = Dump(DMP_PATH)
    exc = dump.exc
    if exc is None:
        raise SystemExit('no exception stream')

    exe_base = None
    for base, msize, name in dump.mods:
        if name.lower().startswith('streamline'):
            exe_base, exe_size = base, msize
    if exe_base is None:
        raise SystemExit('Streamline.exe not found in module list')
    exe_hi = exe_base + exe_size

    def in_exe(va):
        return exe_base <= va < exe_hi

    def is_exec(va):
        m = dump.module_of(va)
        if m is None:
            return False
        name, base, _ = m
        if name.lower().startswith('streamline'):
            return pe.is_exec_rva(va - base)
        return (va - base) >= 0x1000

    def resync(cur_rip, cur_rsp):
        a, end = cur_rsp, cur_rsp + SCAN_LIMIT
        while a < end:
            v = dump.read_q(a)
            if v is None:
                return None, None
            if v != cur_rip and is_exec(v):
                return v, a + 8
            a += 8
        return None, None

    rows = []
    rip, rsp = exc['rip'], exc['rsp']
    for i in range(MAX_FRAMES):
        m = dump.module_of(rip)
        if in_exe(rip):
            fva = IMAGE_BASE + (rip - exe_base)
            rf = pe.lookup_function(rip - exe_base, use_prev=True)
            fn = 'FUN_%x' % (IMAGE_BASE + rf[0]) if rf else '<no .pdata entry>'
            adj = pe.stack_adjustment(rip - exe_base)
            slot = rsp + adj
            rows.append(dict(i=i, rip=rip, fva=fva, fn=fn, fn_begin=rf[0] if rf else None,
                             adj=adj, slot=slot, ra=None))
            ra = dump.read_q(slot)
            rows[-1]['ra'] = ra
            if ra is None:
                rows.append(dict(i=i + 1, rip=None, fva='-', fn='(unreadable stack)',
                                 adj=None, slot=None, ra=None))
                break
            rip, rsp = ra, slot + 8
        else:
            mod = '%s+0x%x' % (m[0], rip - m[1]) if m else 'UNKNOWN'
            rows.append(dict(i=i, rip=rip, fva='-', fn=mod, fn_begin=None,
                             adj=None, slot=None, ra=None))
            nrip, nrsp = resync(rip, rsp)
            if nrip is None:
                rows.append(dict(i=i + 1, rip=None, fva='-',
                                 fn='(no code ptr in 0x%x)' % SCAN_LIMIT,
                                 adj=None, slot=None, ra=None))
                break
            rip, rsp = nrip, nrsp
        if dump.module_of(rip) is None:
            rows.append(dict(i=i + 1, rip=rip, fva='-',
                             fn='UNKNOWN (outside modules)', adj=None, slot=None, ra=None))
            break

    # ---- transition check: every exe RA must be the return address of a
    #      `call` in the caller that targets the frame's own function ----
    checks = []
    skipped = []
    n_ok = n_chain = n_ind = n_bad = 0
    for r in rows:
        ra = r.get('ra')
        if ra is None or not isinstance(r.get('fva'), int):
            continue
        if not (exe_base <= ra < exe_hi):
            m = dump.module_of(ra)
            checks.append('%-4d %-16s RA %016x -> outside Streamline.exe (%s)'
                          % (r['i'], r['fn'], ra, m[0] if m else 'unmapped'))
            continue
        ra_rva = ra - exe_base
        crf = pe.lookup_function(ra_rva, use_prev=True)
        if crf is None:
            checks.append('%-4d %-16s RA fileVA=0x%x not inside any .pdata entry'
                          % (r['i'], r['fn'], IMAGE_BASE + ra_rva))
            continue
        caller = 'FUN_%x' % (IMAGE_BASE + crf[0])
        tgt = pe.call_target(ra_rva)
        if tgt is None:
            n_ind += 1
            checks.append('%-4d %-16s RA fileVA=0x%x in %-14s indirect call '
                          '(target unverifiable)'
                          % (r['i'], r['fn'], IMAGE_BASE + ra_rva, caller))
            continue
        tgt_rf = pe.lookup_function(tgt, use_prev=False)
        tgt_begin = tgt_rf[0] if tgt_rf else None
        if tgt_begin is None:
            n_ok += 1
            tag = 'OK (callee has no .pdata entry there: thunk/stub)'
        elif tgt_begin == r['fn_begin']:
            n_ok += 1
            tag = 'OK'
        elif tgt_begin in pe.chain_begins(r['fn_begin']):
            n_chain += 1
            tag = 'OK (chained .pdata chunk of the frame)'
        else:
            n_bad += 1
            skipped.append((r['i'], 'FUN_%x' % (IMAGE_BASE + tgt), r['fn'],
                            IMAGE_BASE + ra_rva, caller))
            checks.append(
                '%-4d %-16s RA fileVA=0x%x in %-14s call@0x%x -> FUN_%x  '
                'SKIPPED FRAME (tail call): that callee is not on the stack'
                % (r['i'], r['fn'], IMAGE_BASE + ra_rva, caller,
                   IMAGE_BASE + ra_rva - 5, IMAGE_BASE + tgt))
            continue
        checks.append('%-4d %-16s RA fileVA=0x%x in %-14s call@0x%x -> %-16s %s'
                      % (r['i'], r['fn'], IMAGE_BASE + ra_rva, caller,
                         IMAGE_BASE + ra_rva - 5, 'FUN_%x' % (IMAGE_BASE + tgt), tag))

    out = []
    out.append('fault thread tid=%d' % exc['tid'])
    out.append('exception code=%08x addr=%016x nparams=%d' %
               (exc['code'], exc['addr'], exc['nparams']))
    out.append('context rip=%016x rsp=%016x' % (exc['rip'], exc['rsp']))
    out.append('Streamline.exe runtime base=%016x size=%x   (file image base=%016x)'
               % (exe_base, exe_size, IMAGE_BASE))
    out.append('')
    out.append('%-4s %-18s %-18s %-24s %-18s %s' %
               ('#', 'runtime RIP', 'fileVA', 'function', 'RA slot (rsp+adj)', 'via'))
    out.append('-' * 118)
    for r in rows:
        rip_s = '%016x' % r['rip'] if isinstance(r['rip'], int) else '(stop)'
        fva_s = 'fileVA=0x%x' % r['fva'] if isinstance(r['fva'], int) else r['fva']
        slot_s = '%012x' % r['slot'] if r.get('slot') is not None else '-'
        if r.get('adj') is not None:
            via = 'adj=0x%x' % r['adj']
        elif isinstance(r['rip'], int):
            via = 'leaf/resync'
        else:
            via = ''
        out.append('%-4s %-18s %-18s %-24s %-18s %s' %
                   (r['i'], rip_s, fva_s, r['fn'], slot_s, via))
    out.append('')
    out.append('transition check (exe frames: the return address at the frame\'s '
               'slot must be a `call rel32` return inside the caller, and that '
               'call must reach the frame\'s own function)')
    out.append('-' * 118)
    out.extend(checks)
    out.append('')
    out.append('%d direct match(es) [%d through chained .pdata chunks], '
               '%d indirect call(s) (target not statically verifiable), '
               '%d skipped frame(s)' % (n_ok + n_chain, n_chain, n_ind, n_bad))
    for idx, callee, frame, ra_fva, caller in skipped:
        out.append('')
        out.append('note: frame %s is %s, but the return address at its slot comes from a'
                   % (idx, frame))
        out.append('      `call` to %s (site: fileVA 0x%x in %s, return address '
                   'fileVA 0x%x).' % (callee, ra_fva - 5, caller, ra_fva))
        out.append('      %s is not present on the stack, and both functions would'
                   % callee)
        out.append('      occupy one and the same entry rsp, so %s left without' % callee)
        out.append('      pushing a frame: it restored its own frame and tail-jumped into')
        out.append('      %s (a plain `ret` would have returned into %s). The chain is'
                   % (frame, caller))
        out.append('      therefore complete; %s is hidden by the tail call.' % callee)
    text = '\n'.join(out) + '\n'
    sys.stdout.write(text)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, 'w') as f:
        f.write(text)
    sys.stderr.write('wrote %s\n' % OUT_PATH)


if __name__ == '__main__':
    main()
