#!/usr/bin/env python3
"""The repository root must contain only the published set.

A stray file at the root is invisible to every test that looks in `tests/` and `tools/`, yet it ships: one
mis-typed `cp` once left a duplicate test module at the root of the public repository, where it did nothing and
confused the tree. This checks the tracked root entries against an allow-list.

The same test runs in the lab worktree, which legitimately carries agent tooling and notes; the strict list is
applied when the checkout has no `scripts/` directory, which only the published tree lacks.
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
LAB_ONLY = {
    "AGENTS.md", "SUBAGENT_RULES.md", "LAB_BACKLOG.md", "MODLOG.md", "scripts", "runtime",
    "docs", "game", "wiki", "requirements.txt", "servers.txt",
}


def tracked_root_entries():
    result = subprocess.run(["git", "-C", ROOT, "ls-files"], capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        return None
    entries = set()
    for line in result.stdout.splitlines():
        entries.add(line.split("/", 1)[0])
    return entries


@unittest.skipUnless(os.path.exists(os.path.join(ROOT, ".git")), "not a git checkout")
class RepoLayoutTest(unittest.TestCase):
    def test_the_root_carries_nothing_unexpected(self):
        entries = tracked_root_entries()
        if entries is None:
            self.skipTest("git ls-files failed")
        allowed = PUBLISHED | LAB_ONLY
        unexpected = sorted(entry for entry in entries if entry not in allowed)
        self.assertEqual(unexpected, [], "unexpected tracked entry at the repository root: %s" % unexpected)

    def test_the_published_checkout_carries_no_lab_files(self):
        entries = tracked_root_entries()
        if entries is None:
            self.skipTest("git ls-files failed")
        if os.path.isdir(os.path.join(ROOT, "scripts")):
            self.skipTest("this is the lab worktree, which carries agent tooling on purpose")
        leaked = sorted(entry for entry in entries if entry in LAB_ONLY)
        self.assertEqual(leaked, [], "lab-only material in the published tree: %s" % leaked)

    def test_the_stray_root_module_is_gone(self):
        # The specific file that prompted this test: a duplicate of tests/test_watch_resource_keys.py.
        self.assertFalse(os.path.exists(os.path.join(ROOT, "test_watch_resource_keys.py")),
                         "a duplicate test module is present at the repository root")


if __name__ == "__main__":
    unittest.main()
