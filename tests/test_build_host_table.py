#!/usr/bin/env python3
"""Pin `tools/build_host.py`'s patch table.

That table is the one place where a stale byte string turns into a corrupted client: `main` asserts each
`original` before writing, but only for the patches a caller selects, so an entry nobody has used lately can rot
unnoticed. These tests check the whole table at once — structure everywhere, and the original bytes against the
shipped image when it is present.

The structural checks run in any checkout; the byte checks skip without `game/`.
"""

import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)
sys.path.insert(0, os.path.join(ROOT, "tools"))

try:
    import build_host  # noqa: E402
except Exception:  # pragma: no cover - the public tree ships the tool
    build_host = None

SHIPPED = os.path.join(ROOT, "game", "hns", "Binaries", "Win64", "Streamline.exe")


@unittest.skipUnless(build_host is not None, "build_host tool unavailable")
class PatchTableStructureTest(unittest.TestCase):
    def test_every_entry_has_equal_length_halves(self):
        for name, (offset, old, new) in build_host.BASE.items():
            with self.subTest(patch=name):
                self.assertEqual(len(bytes.fromhex(old)), len(bytes.fromhex(new)),
                                 "%s: replacement length differs from the original" % name)

    def test_every_entry_actually_changes_something(self):
        for name, (offset, old, new) in build_host.BASE.items():
            with self.subTest(patch=name):
                self.assertNotEqual(old.lower(), new.lower(), "%s is a no-op" % name)

    def test_named_sets_reference_real_entries(self):
        for group in ("STD", "CONTESTANTS"):
            for name in getattr(build_host, group):
                with self.subTest(group=group, patch=name):
                    self.assertIn(name, build_host.BASE)

    def test_no_two_patches_overlap(self):
        spans = sorted((offset, offset + len(bytes.fromhex(old)), name)
                       for name, (offset, old, _new) in build_host.BASE.items())
        for (_start, end, name), (next_start, _next_end, next_name) in zip(spans, spans[1:]):
            with self.subTest(patch=name, next=next_name):
                self.assertLessEqual(end, next_start,
                                     "%s and %s overlap" % (name, next_name))

    def test_offsets_are_inside_a_sixty_six_megabyte_image(self):
        # Cheap sanity check that runs without the exe: a typo'd offset is usually wildly out of range.
        for name, (offset, _old, _new) in build_host.BASE.items():
            with self.subTest(patch=name):
                self.assertGreater(offset, 0x400, name)
                self.assertLess(offset, 0x4000000, name)


@unittest.skipUnless(build_host is not None and os.path.exists(SHIPPED),
                     "shipped exe is absent (game/ is not in the repo)")
class PatchTableVersusImageTest(unittest.TestCase):
    def test_every_original_matches_the_shipped_image(self):
        with open(SHIPPED, "rb") as handle:
            data = handle.read()
        for name, (offset, old, _new) in build_host.BASE.items():
            with self.subTest(patch=name):
                want = bytes.fromhex(old)
                got = data[offset:offset + len(want)]
                self.assertEqual(got, want,
                                 "%s at 0x%X: table says %s, image has %s"
                                 % (name, offset, old, got.hex()))

    def test_most_patches_sit_in_the_text_section(self):
        # The table's offsets are file offsets; the majority must land in .text, which is where the patch
        # sites listed on the wiki live. A table that drifted wholesale would break this cheaply.
        text_limit = 0x2CC2200
        in_text = sum(1 for offset, _old, _new in build_host.BASE.values() if 0x400 <= offset < text_limit)
        self.assertGreater(in_text, len(build_host.BASE) // 2,
                           "only %d of %d patches are in .text" % (in_text, len(build_host.BASE)))


if __name__ == "__main__":
    unittest.main()
