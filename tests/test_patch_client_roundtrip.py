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

    def test_the_host_play_profile_maps_its_cave_sections(self):
        """The header half of the patcher: raising VirtualSize and setting MEM_EXECUTE for cave sections.

        `host-play` is the profile that needs it — its caves live in `.rdata` and `.pdata` tails, which are not
        mapped executable as shipped. The expectations come from the patcher's own section table, so a table edit
        cannot silently disagree with the test.
        """
        out = os.path.join(self.work, "host-play.exe")
        result = self._run("--profile", "host-play", "--in", self.src, "--out", out)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        with open(out, "rb") as handle:
            patched = handle.read()

        profile = pc.PROFILES["host-play"]
        for site in profile.sites:
            self.assertEqual(patched[site.offset:site.offset + site.size], site.patched,
                             "%s was not written" % site.name)

        before = {section.name: section for section in pc.sections(self.original)}
        after = {section.name: section for section in pc.sections(patched)}
        held = set()
        for site in profile.sites:
            section = pc.section_for_file_offset(pc.sections(patched), site.offset)
            if site.name in pc.CAVE_BODIES:
                held.add(section.name)

        self.assertTrue(held, "host-play installs no cave bodies, so this test proves nothing")
        for name in held:
            _shipped, mapped = pc.CAVE_SECTIONS[name]
            self.assertEqual(after[name].vsize, mapped,
                             "%s VirtualSize was not raised to the mapped size" % name)
            self.assertTrue(int.from_bytes(patched[after[name].header + 36:after[name].header + 40], "little")
                            & pc.MEM_EXECUTE, "%s was not made executable" % name)
            self.assertEqual(before[name].vsize, pc.CAVE_SECTIONS[name][0],
                             "the source is not the supported build for %s" % name)

        # The DRM cave is in `.text`, which is already mapped executable: its size must not move.
        self.assertEqual(after[".text"].vsize, before[".text"].vsize,
                         "a profile changed .text's VirtualSize")

        checksum_at = pec.checksum_offset(patched)
        stored = int.from_bytes(patched[checksum_at:checksum_at + 4], "little")
        self.assertEqual(stored, pec.compute_checksum(patched), "checksum not recomputed")

        verify = self._run("--profile", "host-play", "--verify", out)
        self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)

    def test_re_patching_an_already_patched_file_is_refused(self):
        twice = os.path.join(self.work, "twice.exe")
        result = self._run("--profile", "host-min", "--in", self.out, "--out", twice)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("already patched", result.stdout + result.stderr)
        self.assertFalse(os.path.exists(twice), "a refused patch still wrote an output file")

    def test_the_patch_table_can_be_listed(self):
        result = self._run("--profile", "list")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("login_startplay_gate", result.stdout)


if __name__ == "__main__":
    unittest.main()
