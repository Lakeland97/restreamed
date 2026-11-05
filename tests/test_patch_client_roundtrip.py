#!/usr/bin/env python3
"""End-to-end check of `tools/patch_client.py` on a copy of the shipped executable.

This is the patcher a user actually runs, and it does four things that can each go wrong silently: verify the
original bytes, rewrite them, inject caves and raise section sizes, then recompute the PE checksum. The test
drives it on a throwaway copy — never the installed exe — and checks the outcomes rather than the messages:

  * the copy changes, and a sampled site holds the replacement bytes;
  * the checksum field equals the specification's value for the patched file (`fix_pe_checksum` is imported by
    the patcher, so this also pins that the two agree);
  * the source file is untouched;
  * `--verify` passes on the result and `--detect` recognises it;
  * re-patching an already-patched file is refused instead of double-applying.

`host-min` is used deliberately: byte patches only, no cave injection, so the test stays fast while still
covering the write path. Skips without `game/`.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)
TOOL = os.path.join(ROOT, "tools", "patch_client.py")
SHIPPED = os.path.join(ROOT, "game", "hns", "Binaries", "Win64", "Streamline.exe")

sys.path.insert(0, os.path.join(ROOT, "tools"))
try:
    import fix_pe_checksum as pec  # noqa: E402
    import patch_client as pc      # noqa: E402
except Exception:  # pragma: no cover
    pec = pc = None


@unittest.skipUnless(os.path.exists(SHIPPED) and pec is not None and pc is not None,
                     "shipped exe is absent (game/ is not in the repo)")
class PatchClientRoundTripTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.mkdtemp(prefix="patch_client_")
        cls.src = os.path.join(cls.work, "in.exe")
        cls.out = os.path.join(cls.work, "out.exe")
        shutil.copyfile(SHIPPED, cls.src)
        with open(cls.src, "rb") as handle:
            cls.original = handle.read()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work, ignore_errors=True)

    def _run(self, *args, timeout=300):
        return subprocess.run([sys.executable, TOOL, *args], capture_output=True, text=True, timeout=timeout)

    def test_one_patch_a_copy_verify_and_detect(self):
        result = self._run("--profile", "host-min", "--in", self.src, "--out", self.out)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        with open(self.out, "rb") as handle:
            patched = handle.read()
        self.assertEqual(len(patched), len(self.original), "the image changed size")
        self.assertNotEqual(patched, self.original, "the patched copy is identical to the source")

        # The checksum the patcher wrote must be the one the specification derives from the patched bytes.
        offset = pec.checksum_offset(patched)
        stored = int.from_bytes(patched[offset:offset + 4], "little")
        self.assertEqual(stored, pec.compute_checksum(patched), "checksum not recomputed after patching")

        # A site the profile definitely carries: login_startplay_gate (wiki H1) is 84c0 -> 31c0.
        self.assertEqual(patched[0x7B1E2E:0x7B1E30], bytes.fromhex("31c0"))

        with open(self.src, "rb") as handle:
            self.assertEqual(handle.read(), self.original, "the source file was modified")

        verify = self._run("--profile", "host-min", "--verify", self.out)
        self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)
        self.assertIn("VERDICT: PASS", verify.stdout)

        detect = self._run("--detect", self.out)
        self.assertIn("host-min", detect.stdout, detect.stdout)

    def test_the_client_profile_injects_its_cave(self):
        """The cave path end to end: a body lands, its hooks jump to it, and the image shape does not move.

        `host-min` above is byte patches only. `client` is the smallest profile that injects a cave body (the DRM
        FakeID stub in `.text`), so it covers the copy-a-stub-into-a-section-tail path that every other profile
        builds on. The cave sits in the page `.text` already maps, so VirtualSize must stay put.
        """
        out = os.path.join(self.work, "client.exe")
        result = self._run("--profile", "client", "--in", self.src, "--out", out)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        with open(out, "rb") as handle:
            patched = handle.read()

        cave = pc.SITE_BY_NAME["drm_cave"]
        self.assertEqual(patched[cave.offset:cave.offset + cave.size], cave.patched,
                         "the cave body was not written at its offset")

        for name in ("drm_entry_a", "drm_entry_b", "drm_entry_c"):
            hook = pc.SITE_BY_NAME[name]
            self.assertEqual(patched[hook.offset:hook.offset + hook.size], hook.patched,
                             "%s was not written" % name)
            self.assertEqual(hook.patched[0], 0xE9, "%s is not a jump" % name)

        before = {section.name: section.vsize for section in pc.sections(self.original)}
        after = {section.name: section.vsize for section in pc.sections(patched)}
        self.assertEqual(after[".text"], before[".text"],
                         "the client profile moved .text's VirtualSize; its cave is already in a mapped page")

        checksum_at = pec.checksum_offset(patched)
        stored = int.from_bytes(patched[checksum_at:checksum_at + 4], "little")
        self.assertEqual(stored, pec.compute_checksum(patched),
                         "checksum not recomputed for the cave profile")

        verify = self._run("--profile", "client", "--verify", out)
        self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)
        self.assertIn("VERDICT: PASS", verify.stdout)

        # A damaged cave body must be reported, not tolerated: --verify has to compare the stub itself, not just
        # the hooks that jump to it.
        tampered = os.path.join(self.work, "client-tampered.exe")
        data = bytearray(patched)
        data[cave.offset + 40] ^= 0xFF
        with open(tampered, "wb") as handle:
            handle.write(bytes(data))
        damaged = self._run("--profile", "client", "--verify", tampered)
        self.assertNotEqual(damaged.returncode, 0, damaged.stdout + damaged.stderr)
        self.assertIn("VERDICT: FAIL", damaged.stdout)
        self.assertIn(cave.name, damaged.stdout)

    def test_cave_sections_are_mapped_for_the_profiles_that_use_them(self):
        """The header half of the patcher, over every section that holds a cave body.

        `host-play` covers `.rdata` (raised to its raw size and made executable); `all-cosmetics-equip` adds
        `.pdata`, whose tail arithmetic differs. `.text` is asserted separately: its DRM cave sits in a page the
        section already maps, so its VirtualSize must not move. Expectations come from the patcher's own
        `CAVE_SECTIONS`, so a table edit cannot disagree with a stale test.
        """
        cases = (("host-play", "host-play.exe", {".rdata"}),
                 ("all-cosmetics-equip", "equip.exe", {".rdata", ".pdata"}))
        for profile_name, filename, expected in cases:
            with self.subTest(profile=profile_name):
                out = os.path.join(self.work, filename)
                result = self._run("--profile", profile_name, "--in", self.src, "--out", out)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

                with open(out, "rb") as handle:
                    patched = handle.read()

                profile = pc.PROFILES[profile_name]
                for site in profile.sites:
                    self.assertEqual(patched[site.offset:site.offset + site.size], site.patched,
                                     "%s: %s was not written" % (profile_name, site.name))

                before = {section.name: section for section in pc.sections(self.original)}
                after = {section.name: section for section in pc.sections(patched)}
                held = set()
                for site in profile.sites:
                    if site.name in pc.CAVE_BODIES:
                        held.add(pc.section_for_file_offset(pc.sections(patched), site.offset).name)

                self.assertEqual(held, expected,
                                 "%s no longer exercises %s" % (profile_name, expected - held))
                for name in held:
                    _shipped, mapped = pc.CAVE_SECTIONS[name]
                    self.assertEqual(after[name].vsize, mapped,
                                     "%s: %s VirtualSize was not raised to the mapped size" % (profile_name, name))
                    self.assertTrue(int.from_bytes(patched[after[name].header + 36:
                                                           after[name].header + 40], "little")
                                    & pc.MEM_EXECUTE, "%s: %s was not made executable" % (profile_name, name))
                    self.assertEqual(before[name].vsize, pc.CAVE_SECTIONS[name][0],
                                     "the source is not the supported build for %s" % name)

                self.assertEqual(after[".text"].vsize, before[".text"].vsize,
                                 "%s changed .text's VirtualSize" % profile_name)

                checksum_at = pec.checksum_offset(patched)
                stored = int.from_bytes(patched[checksum_at:checksum_at + 4], "little")
                self.assertEqual(stored, pec.compute_checksum(patched), "checksum not recomputed")

                verify = self._run("--profile", profile_name, "--verify", out)
                self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)

    def test_re_patching_an_already_patched_file_is_refused(self):
        twice = os.path.join(self.work, "twice.exe")
        result = self._run("--profile", "host-min", "--in", self.out, "--out", twice)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("already patched", result.stdout + result.stderr)
        self.assertFalse(os.path.exists(twice), "a refused patch still wrote an output file")

    def test_add_and_drop_shape_a_profile(self):
        """`--add`/`--drop` are user-facing, and adding a cave has to switch the section mapping on.

        `host-min` carries no caves, so adding a whole cave group through the CLI exercises the derivation of
        `maps_rdata_exec` for a custom profile — the part a user cannot see except as a crash if it is wrong.
        """
        custom = os.path.join(self.work, "custom.exe")
        result = self._run("--profile", "host-min", "--add", "identity_cave", "--add", "identity_hook",
                           "--add", "contestants_phase", "--in", self.src, "--out", custom)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        with open(custom, "rb") as handle:
            patched = handle.read()
        sections = {section.name: section for section in pc.sections(patched)}
        self.assertEqual(sections[".rdata"].vsize, pc.CAVE_SECTIONS[".rdata"][1],
                         "adding a cave through --add did not map its section")
        self.assertTrue(int.from_bytes(patched[sections[".rdata"].header + 36:
                                               sections[".rdata"].header + 40], "little")
                        & pc.MEM_EXECUTE, "adding a cave through --add did not make .rdata executable")
        hook = pc.SITE_BY_NAME["identity_hook"]
        self.assertEqual(patched[hook.offset:hook.offset + hook.size], hook.patched)

        # Half a cave group is refused, because a hook without its stub jumps into unwritten bytes.
        half = self._run("--profile", "host-min", "--add", "identity_cave",
                         "--in", self.src, "--out", os.path.join(self.work, "half.exe"))
        self.assertNotEqual(half.returncode, 0, half.stdout + half.stderr)
        self.assertIn("incomplete", half.stdout + half.stderr)

        unknown = self._run("--profile", "host-min", "--add", "no_such_site",
                            "--in", self.src, "--out", os.path.join(self.work, "unknown.exe"))
        self.assertNotEqual(unknown.returncode, 0, unknown.stdout + unknown.stderr)
        self.assertIn("unknown site", unknown.stdout + unknown.stderr)

    def test_the_patch_table_can_be_listed(self):
        result = self._run("--profile", "list")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("login_startplay_gate", result.stdout)


if __name__ == "__main__":
    unittest.main()
