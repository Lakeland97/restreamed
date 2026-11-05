#!/usr/bin/env python3
"""The published repository root must contain only the published set.

A stray file at the root is invisible to every test that looks in `tests/` and `tools/`, yet it ships: a
mis-typed copy once left a duplicate test module at the root of this repository, where no runner looks and where
it still went out with the tree. This checks the tracked root entries against the published set.

The check applies only to a published checkout, which is the one without a `scripts/` directory; the development
worktree carries its own tooling beside the code and is not this test's business.
"""

import os
import subprocess
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)

PUBLISHED = {
    ".gitignore", "CONTRIBUTING.md", "LICENSE", "README", "README.md",
    "mock_appserver.py", "tests", "tools", ".github",
}

IS_PUBLISHED = not os.path.isdir(os.path.join(ROOT, "scripts"))


def tracked_root_entries():
    result = subprocess.run(["git", "-C", ROOT, "ls-files"], capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        return None
    return {line.split("/", 1)[0] for line in result.stdout.splitlines()}


@unittest.skipUnless(IS_PUBLISHED, "development worktree: its root carries tooling by design")
class RepoLayoutTest(unittest.TestCase):
    def test_the_root_carries_only_the_published_set(self):
        entries = tracked_root_entries()
        if entries is None:
            self.skipTest("git ls-files failed")
        unexpected = sorted(entry for entry in entries if entry not in PUBLISHED)
        self.assertEqual(unexpected, [], "unexpected tracked entry at the repository root: %s" % unexpected)

    def test_no_loose_test_module_sits_at_the_root(self):
        entries = tracked_root_entries() or set()
        loose = sorted(entry for entry in entries if entry.startswith("test_") and entry.endswith(".py"))
        self.assertEqual(loose, [], "test modules belong in tests/, not at the root: %s" % loose)

    def test_the_file_that_prompted_this_is_gone(self):
        self.assertFalse(os.path.exists(os.path.join(ROOT, "test_watch_resource_keys.py")),
                         "a duplicate test module is present at the repository root")


if __name__ == "__main__":
    unittest.main()
