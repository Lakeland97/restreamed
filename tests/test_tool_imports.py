#!/usr/bin/env python3
"""Every published tool must be importable without running its CLI.

A tool that executes at import time cannot be used from a test harness or another tool, and it breaks the moment
anyone imports it. `memdump` and `throwscan` ended with a bare `main()`, and `unwind` parsed `sys.argv` at module
level (raising `SystemExit(2)` on import); all three are guarded now. Each import runs in a subprocess so one
failure cannot poison the others.
"""

import glob
import os
import subprocess
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)
TOOLS = os.path.join(ROOT, "tools")


class ToolImportTest(unittest.TestCase):
    def test_every_published_tool_imports_cleanly(self):
        names = sorted(os.path.basename(path)[:-3] for path in glob.glob(os.path.join(TOOLS, "*.py"))
                       if not os.path.basename(path).startswith("_"))
        self.assertGreaterEqual(len(names), 15, "the published toolset shrank unexpectedly")
        failures = []
        for name in names:
            result = subprocess.run([sys.executable, "-c", "import %s" % name], cwd=TOOLS,
                                    capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                tail = (result.stderr or result.stdout).strip().splitlines()[-1:]
                failures.append("%s (exit %d): %s" % (name, result.returncode, tail))
        self.assertEqual(failures, [], "tools that run or fail on import")


if __name__ == "__main__":
    unittest.main(verbosity=2)
