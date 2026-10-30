#!/usr/bin/env python3
"""Offline tests for tools/patch_client.py.

No game file is needed. A synthetic PE image carries the same section layout as
the shipped client (so the fixed VAs and file offsets line up) and the few
original bytes each site expects; the patcher cannot tell it from the real
thing. The real client is only ever used by a human running the tool.

What is covered:
  * the checksum routine against an independent implementation and a pinned
    value, and the fix/check round trip;
  * the patch table (unique names, equal sizes, no overlap, VA/offset geometry,
    cave stubs inside the .rdata padding);
  * profile composition (host = host-min + the three caves, group completeness,
    unknown names rejected);
  * apply: every site patched, header edits, checksum, verify PASS, and refusal
    on a mismatched original with nothing written.
"""
import contextlib
import hashlib
import io
import os
import shutil
import struct
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import patch_client as pc  # noqa: E402
import fix_pe_checksum as fpc  # noqa: E402


# The shipped client's layout, as far as the patch sites care:
#   .text  file 0x400 .. 0x2cc2200   (VA 0x140001000 ..)
#   .rdata file 0x2cc2200 .. 0x3ab7e00 (VA 0x142cc3000 ..)
# with 260 bytes of .rdata raw data past its VirtualSize.
IMAGE_SIZE = 0x3AB7E00
TEXT_RAW, TEXT_RAWSIZE = 0x400, 0x2CC1E00
TEXT_VA, TEXT_VSIZE = 0x1000, 0x2CC1D95
RDATA_RAW, RDATA_RAWSIZE = 0x2CC2200, 0x00DF5C00
RDATA_VA, RDATA_VSIZE = 0x2CC3000, 0x00DF5AFC
IMAGE_BASE = 0x140000000


def make_pe(size=IMAGE_SIZE):
    """A minimal but well-formed PE32+ image with empty .text/.rdata sections."""
    data = bytearray(size)
    data[0:2] = b"MZ"
    pe = 0x80
    struct.pack_into("<I", data, 0x3C, pe)
    data[pe:pe + 4] = b"PE\0\0"
    # COFF: machine, nsections, ..., SizeOfOptionalHeader, Characteristics
    struct.pack_into("<HHIIIHH", data, pe + 4, 0x8664, 2, 0, 0, 0, 240, 0x22)
    opt = pe + 24
    struct.pack_into("<H", data, opt, 0x20B)
    struct.pack_into("<Q", data, opt + 24, IMAGE_BASE)
    table = opt + 240
    for i, (name, va, vsize, raw, rawsize, chars) in enumerate((
        (b".text", TEXT_VA, TEXT_VSIZE, TEXT_RAW, TEXT_RAWSIZE, 0x60000020),
        (b".rdata", RDATA_VA, RDATA_VSIZE, RDATA_RAW, RDATA_RAWSIZE, 0x40000040),
    )):
        header = table + i * 40
        data[header:header + 8] = name
        struct.pack_into("<IIII", data, header + 8, vsize, va, rawsize, raw)
        struct.pack_into("<I", data, header + 36, chars)
    return data


def make_client_fixture():
    """The synthetic image with every site's shipped bytes in place."""
    data = make_pe()
    for site in pc.SITES:
        data[site.offset:site.offset + site.size] = site.original
    return data


_fixture = []


def fixture():
    if not _fixture:
        _fixture.append(make_client_fixture())
    return _fixture[0]


def fixture_copy():
    return bytearray(fixture())


def reference_checksum(data):
    """Independent PE checksum: 32-bit sum of little-endian words, folded twice."""
    buf = bytearray(data)
    off = struct.unpack_from("<I", buf, 0x3C)[0] + 0x58
    buf[off:off + 4] = b"\x00\x00\x00\x00"
    total = 0
    n = len(buf)
    for i in range(0, n - (n % 2), 2):
        total = (total + buf[i] + (buf[i + 1] << 8)) & 0xFFFFFFFF
    if n % 2:
        total += buf[-1]
    while total > 0xFFFF:
        total = (total & 0xFFFF) + (total >> 16)
    return (total + n) & 0xFFFFFFFF


# Pinned by the reference implementation above (and by the shipped client:
# the same routine reproduces the stored 0x03ef2055 of the verified host build).
KNOWN_FIXTURE_CHECKSUM = 0x0000B18D
KNOWN_PATCHED_CHECKSUM = 0x03AC218C


class ChecksumTest(unittest.TestCase):
    def test_matches_independent_implementation(self):
        for size in (0x2000, 0x2001, 0x40000):
            data = make_pe(size)
            self.assertEqual(fpc.compute_checksum(data), reference_checksum(data),
                             "size %#x" % size)

    def test_known_value(self):
        data = make_pe(0x2000)
        self.assertEqual(fpc.compute_checksum(data), KNOWN_FIXTURE_CHECKSUM)

    def test_stored_field_does_not_affect_result(self):
        data = make_pe(0x2000)
        off = fpc.checksum_offset(data)
        want = fpc.compute_checksum(data)
        struct.pack_into("<I", data, off, 0xDEADBEEF)
        self.assertEqual(fpc.compute_checksum(data), want)

    def test_odd_tail_and_carry(self):
        data = make_pe(0x2001)
        self.assertEqual(fpc.compute_checksum(data), reference_checksum(data))
        data[0x300:0x400] = b"\xff" * 0x100
        self.assertEqual(fpc.compute_checksum(data), reference_checksum(data))

    def test_fix_then_check_round_trip(self):
        tmp = tempfile.mkdtemp(prefix="patch_client_sum_")
        try:
            path = os.path.join(tmp, "t.exe")
            with open(path, "wb") as fh:
                fh.write(make_pe(0x2000))
            quiet = io.StringIO()
            with contextlib.redirect_stdout(quiet):
                self.assertEqual(fpc.check(path), 1)      # shipped-style: stale value
                self.assertEqual(fpc.fix(path), 0)
                self.assertEqual(fpc.check(path), 0)
            with open(path, "rb") as fh:
                fixed = fh.read()
            self.assertEqual(fpc.compute_checksum(fixed),
                             struct.unpack_from("<I", fixed, fpc.checksum_offset(fixed))[0])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_not_a_pe(self):
        with self.assertRaises(SystemExit):
            fpc.compute_checksum(bytearray(64))


class TableTest(unittest.TestCase):
    def test_real_table_is_valid(self):
        self.assertTrue(pc.validate_table(pc.SITES))

    def test_names_are_unique(self):
        self.assertEqual(len(pc.SITE_BY_NAME), len(pc.SITES))

    def test_geometry_matches_the_synthetic_layout(self):
        self.assertTrue(pc.validate_geometry(bytearray(make_pe()), pc.SITES))

    def test_overlap_rejected(self):
        a = pc.Site("a", 0x140001000, 0x1000, b"\x01\x02", b"\x03\x04", "t")
        b = pc.Site("b", 0x140001001, 0x1001, b"\x01\x02", b"\x03\x04", "t")
        with self.assertRaises(pc.PatchError):
            pc.validate_table([a, b])

    def test_duplicate_name_rejected(self):
        a = pc.Site("a", 0x140001000, 0x1000, b"\x01", b"\x02", "t")
        b = pc.Site("a", 0x140002000, 0x2000, b"\x01", b"\x02", "t")
        with self.assertRaises(pc.PatchError):
            pc.validate_table([a, b])

    def test_size_mismatch_rejected(self):
        with self.assertRaises(pc.PatchError):
            pc._s("bad", 0x140001000, 0x1000, "84c0", "90", "t")

    def test_swapped_va_and_offset_rejected(self):
        site = pc.Site("bad", 0x1000, 0x140001000, b"\x84\xc0", b"\x31\xc0", "t")
        with self.assertRaises(pc.PatchError):
            pc.validate_table([site])

    def test_wrong_table_va_is_caught_against_the_image(self):
        site = pc.Site("moved", 0x1407B2A2F, 0x007B1E2E, b"\x84\xc0", b"\x31\xc0", "t")
        with self.assertRaises(pc.PatchError):
            pc.validate_geometry(bytearray(make_pe()), [site])

    def test_unrecognised_rdata_vsize_is_caught(self):
        data = make_pe()
        rdata = [s for s in pc.sections(data) if s.name == ".rdata"][0]
        struct.pack_into("<I", data, rdata.header + 8, 0x00DF5B00)
        profile = pc.PROFILES["host"]
        with self.assertRaises(pc.PatchError):
            pc.validate_geometry(data, profile.sites)

    def test_cave_stub_outside_the_padding_is_caught(self):
        data = make_pe()
        site = pc.Site("cave", 0x143AB8B60, 0x03AB7E00 - 4, bytes(8), bytes(8), "t")
        with self.assertRaises(pc.PatchError):
            pc.validate_geometry(data, [site])


class ProfileTest(unittest.TestCase):
    def test_host_min_is_the_byte_patch_set(self):
        self.assertEqual(pc.PROFILES["host-min"].site_names, pc.HOST_BYTES)
        self.assertFalse(pc.PROFILES["host-min"].maps_rdata_exec)
        for name in pc.CAVE_SITES + ("round_end_le2", "matchover_m2", "p3_force_loaded"):
            self.assertNotIn(name, pc.PROFILES["host-min"].site_names)

    def test_host_is_host_min_plus_the_caves(self):
        host = set(pc.PROFILES["host"].site_names)
        host_min = set(pc.PROFILES["host-min"].site_names)
        self.assertEqual(host, host_min | set(pc.CAVE_SITES))
        self.assertTrue(pc.PROFILES["host"].maps_rdata_exec)

    def test_host_keeps_the_matchover_clock_gate(self):
        # loop3 relies on the shipped +0x31 gate, so matchover_m2 is not applied.
        self.assertNotIn("matchover_m2", pc.PROFILES["host"].site_names)
        self.assertIn("round_timer_cave", pc.PROFILES["host"].site_names)
        self.assertNotIn("round_end_le2", pc.PROFILES["host"].site_names)

    def test_all_cosmetics_equip_adds_the_selection_caves(self):
        base = set(pc.PROFILES["all-cosmetics"].site_names)
        equip = set(pc.PROFILES["all-cosmetics-equip"].site_names)
        self.assertEqual(
            equip,
            base | {"equip_oob_hook", "equip_fatal_cave",
                    "equip_own_hook", "equip_own_cave"},
        )
        self.assertTrue(pc.PROFILES["all-cosmetics-equip"].maps_rdata_exec)

    def test_equip_stubs_stay_inside_the_shared_hole(self):
        # Both faces of the equip pair must fit the one .rdata hole between the
        # shipped VirtualSize and SizeOfRawData; the profile maps it executable.
        sites = {site.name: site for site in pc.SITES}
        low = 0x143AB8B1B
        high = 0x143AB8B60
        for name in ("equip_fatal_cave", "equip_own_cave"):
            site = sites[name]
            self.assertGreaterEqual(site.va, low)
            self.assertLessEqual(site.va + len(site.patched), high, name)
        self.assertLessEqual(
            sites["equip_fatal_cave"].va + len(sites["equip_fatal_cave"].patched),
            sites["equip_own_cave"].va,
        )

    def test_host_does_not_carry_the_wardrobe_caves(self):
        # The equip pair belongs to the client wardrobe profile; adding a cave
        # group must not silently widen the host set.
        for name in ("equip_oob_hook", "equip_fatal_cave",
                     "equip_own_hook", "equip_own_cave"):
            self.assertNotIn(name, pc.PROFILES["host"].site_names)

    def test_profiles_reference_known_sites_in_table_order(self):
        order = [site.name for site in pc.SITES]
        for profile in pc.PROFILES.values():
            self.assertTrue(set(profile.site_names) <= set(order))
            positions = [order.index(name) for name in profile.site_names]
            self.assertEqual(positions, sorted(positions), profile.name)

    def test_compose_add_and_drop(self):
        prof = pc.compose("host", drop=("round_timer_hook", "round_timer_cave"),
                          add=("round_end_le2",))
        self.assertNotIn("round_timer_cave", prof.site_names)
        self.assertIn("round_end_le2", prof.site_names)
        self.assertTrue(prof.maps_rdata_exec)          # identity + loop caves remain
        prof = pc.compose("host-min", add=("score_serialize_one",))
        self.assertIn("score_serialize_one", prof.site_names)
        self.assertFalse(prof.maps_rdata_exec)

    def test_dropping_every_cave_clears_the_mapping(self):
        prof = pc.compose("host", drop=pc.CAVE_SITES)
        self.assertEqual(set(prof.site_names), set(pc.HOST_BYTES))
        self.assertFalse(prof.maps_rdata_exec)

    def test_unknown_site_rejected(self):
        with self.assertRaises(pc.PatchError):
            pc.compose("host", add=("no_such_site",))

    def test_half_a_cave_group_rejected(self):
        with self.assertRaises(pc.PatchError):
            pc.compose("host", drop=("identity_cave",))
        with self.assertRaises(pc.PatchError):
            pc.compose("host-min", add=("loop3_hook",))


class ApplyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="patch_client_apply_")
        cls.src = os.path.join(cls.tmp, "Streamline.exe")
        with open(cls.src, "wb") as fh:
            fh.write(fixture())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_host_patches_every_site(self):
        data = fixture_copy()
        report = pc.apply_patches(data, pc.PROFILES["host"])
        for site in pc.PROFILES["host"].sites:
            self.assertEqual(bytes(data[site.offset:site.offset + site.size]), site.patched,
                             site.name)
        # one line per site, plus the two header edits and the checksum
        self.assertEqual(len(report), len(pc.PROFILES["host"].sites) + 3)

    def test_host_maps_the_rdata_tail(self):
        data = fixture_copy()
        pc.apply_patches(data, pc.PROFILES["host"])
        rdata = [s for s in pc.sections(data) if s.name == ".rdata"][0]
        self.assertEqual(rdata.vsize, pc.RDATA_VIRTUALSIZE)
        self.assertTrue(pc._u32(data, rdata.header + 36) & pc.MEM_EXECUTE)

    def test_checksum_is_recomputed(self):
        data = fixture_copy()
        pc.apply_patches(data, pc.PROFILES["host"])
        off = fpc.checksum_offset(data)
        self.assertEqual(struct.unpack_from("<I", data, off)[0],
                         fpc.compute_checksum(data))
        # the numeric pin lives in ChecksumTest against a fixed buffer; here the
        # property is that the stored field matches the computed one

    def test_verify_passes_after_apply_and_fails_before(self):
        clean = fixture_copy()
        ok, rows = pc.verify(clean, pc.PROFILES["host"])
        self.assertFalse(ok)
        self.assertTrue(any(not good for _name, good, _detail in rows))

        data = fixture_copy()
        pc.apply_patches(data, pc.PROFILES["host"])
        ok, rows = pc.verify(data, pc.PROFILES["host"])
        self.assertTrue(ok, [(n.name if not isinstance(n, str) else n, d)
                             for n, good, d in rows if not good])

    def test_verify_names_the_damaged_site(self):
        data = fixture_copy()
        pc.apply_patches(data, pc.PROFILES["host"])
        site = pc.SITE_BY_NAME["loop3_cave"]
        data[site.offset] ^= 0xFF
        ok, rows = pc.verify(data, pc.PROFILES["host"])
        self.assertFalse(ok)
        bad = [name if isinstance(name, str) else name.name
               for name, good, _detail in rows if not good]
        self.assertIn("loop3_cave", bad)
        self.assertIn("PE checksum", bad)               # the stray byte breaks the sum too
        self.assertNotIn("round_timer_cave", bad)

    def test_mismatched_original_aborts_without_touching_the_image(self):
        data = fixture_copy()
        before = bytes(data)
        site = pc.SITE_BY_NAME["register_server_skip"]
        data[site.offset] = 0x00
        corrupt = bytes(data)
        with self.assertRaises(pc.PatchError) as ctx:
            pc.apply_patches(data, pc.PROFILES["host"])
        self.assertIn(site.name, str(ctx.exception))
        self.assertEqual(bytes(data), corrupt)          # nothing written
        self.assertNotEqual(before, corrupt)

    def test_already_patched_input_is_reported(self):
        data = fixture_copy()
        pc.apply_patches(data, pc.PROFILES["host"])
        with self.assertRaises(pc.PatchError) as ctx:
            pc.apply_patches(data, pc.PROFILES["host"])
        self.assertIn("already patched", str(ctx.exception))

    def test_apply_file_writes_a_verified_copy(self):
        out = os.path.join(self.tmp, "Streamline.host.exe")
        self.addCleanup(lambda: os.path.exists(out) and os.remove(out))
        with open(self.src, "rb") as fh:
            source_sha1 = hashlib.sha1(fh.read()).hexdigest()
        result = pc.apply_file(self.src, out, pc.PROFILES["host"])
        self.assertEqual(result["source_sha1"], source_sha1)
        with open(out, "rb") as fh:
            written = fh.read()
        self.assertEqual(result["output_sha1"], hashlib.sha1(written).hexdigest())
        self.assertEqual(result["checksum"], fpc.compute_checksum(written))
        ok, _rows = pc.verify_file(out, pc.PROFILES["host"])
        self.assertTrue(ok)
        with open(self.src, "rb") as fh:                 # the input is untouched
            self.assertEqual(hashlib.sha1(fh.read()).hexdigest(), source_sha1)

    def test_apply_file_refuses_and_writes_nothing(self):
        bad = os.path.join(self.tmp, "bad.exe")
        out = os.path.join(self.tmp, "bad.out.exe")
        self.addCleanup(lambda: os.path.exists(bad) and os.remove(bad))
        with open(self.src, "rb") as fh:
            data = bytearray(fh.read())
        site = pc.SITE_BY_NAME["wait_two"]
        data[site.offset + 3] ^= 0xFF
        with open(bad, "wb") as fh:
            fh.write(data)
        with self.assertRaises(pc.PatchError) as ctx:
            pc.apply_file(bad, out, pc.PROFILES["host"])
        self.assertIn("wait_two", str(ctx.exception))
        self.assertFalse(os.path.exists(out))
        self.assertFalse(os.path.exists(out + ".tmp"))

    def test_apply_file_refuses_to_overwrite_its_input(self):
        with self.assertRaises(pc.PatchError):
            pc.apply_file(self.src, self.src, pc.PROFILES["host"])

    def test_host_min_drops_the_caves(self):
        data = fixture_copy()
        pc.apply_patches(data, pc.PROFILES["host-min"])
        rdata = [s for s in pc.sections(data) if s.name == ".rdata"][0]
        self.assertEqual(rdata.vsize, RDATA_VSIZE)        # no mapping
        self.assertNotIn(pc.MEM_EXECUTE, (pc._u32(data, rdata.header + 36),))
        for name in pc.CAVE_SITES:
            site = pc.SITE_BY_NAME[name]
            self.assertEqual(bytes(data[site.offset:site.offset + site.size]), site.original)


class CliTest(unittest.TestCase):
    def _run(self, argv):
        """Run main(); returns (exit code, stdout + stderr)."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = pc.main(argv)
        return code, out.getvalue()

    def test_list_prints_every_site(self):
        code, text = self._run(["--profile", "list"])
        self.assertEqual(code, 0)
        for site in pc.SITES:
            self.assertIn(site.name, text)
            self.assertIn("%#010x" % site.offset, text)
            self.assertIn("%#010x" % site.va, text)
        self.assertIn("host-min", text)

    def test_apply_and_verify(self):
        tmp = tempfile.mkdtemp(prefix="patch_client_cli_")
        try:
            src = os.path.join(tmp, "Streamline.exe")
            out = os.path.join(tmp, "Streamline.host.exe")
            with open(src, "wb") as fh:
                fh.write(fixture())
            code, text = self._run(["--profile", "host", "--in", src, "--out", out])
            self.assertEqual(code, 0, text)
            self.assertIn("wrote " + out, text)
            code, text = self._run(["--profile", "host", "--verify", out])
            self.assertEqual(code, 0, text)
            self.assertIn("VERDICT: PASS", text)

            # a host-min output is missing the caves, so the host profile must fail it
            minout = os.path.join(tmp, "Streamline.host-min.exe")
            code, text = self._run(["--profile", "host-min", "--in", src, "--out", minout])
            self.assertEqual(code, 0, text)
            code, text = self._run(["--profile", "host", "--verify", minout])
            self.assertEqual(code, 1, text)
            self.assertIn("VERDICT: FAIL", text)
            self.assertIn("loop3_cave", text)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_mismatch_exit_code_and_no_output_file(self):
        tmp = tempfile.mkdtemp(prefix="patch_client_cli_bad_")
        try:
            src = os.path.join(tmp, "Streamline.exe")
            out = os.path.join(tmp, "out.exe")
            data = make_pe()
            with open(src, "wb") as fh:
                fh.write(data)                            # no originals at all
            code, text = self._run(["--profile", "host", "--in", src, "--out", out])
            self.assertEqual(code, 1)
            self.assertIn("refusing to write", text)
            self.assertIn("login_startplay_gate", text)
            self.assertFalse(os.path.exists(out))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_unknown_profile_is_a_usage_error(self):
        with self.assertRaises(SystemExit):
            self._run(["--profile", "nope", "--in", "x", "--out", "y"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
