#!/usr/bin/env python3
"""Run every offline test in tests/ with one command.

pytest is not a dependency. The runner executes both styles:

  * `unittest.TestCase` classes discovered in every `tests/test_*.py`
  * module-level `test_*` callables that are not part of a TestCase

The public tree ships the self-contained modules; the lab tree adds artifact-backed
modules, so the check total differs. An import failure is reported and counts as a
failure, not a skip.

No client and no game: these drive `mock_appserver.py` on ephemeral ports and parse
its replies.
"""

import importlib
import os
import sys
import traceback
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
sys.path.insert(0, TESTS_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))


def _test_modules():
    for name in sorted(os.listdir(TESTS_DIR)):
        if name.startswith("test_") and name.endswith(".py"):
            yield name[:-3]


def main():
    modules = list(_test_modules())
    loaded = []
    import_errors = []
    for name in modules:
        try:
            loaded.append(importlib.import_module(name))
        except Exception:                                   # noqa: BLE001 - report, do not hide
            import_errors.append((name, traceback.format_exc()))

    suite = unittest.TestSuite()
    for module in loaded:
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))

    result = unittest.TextTestRunner(verbosity=1).run(suite)

    # pytest-style module-level functions (test_hxinflate.py).
    function_failures = []
    function_count = 0
    for module in loaded:
        for attr in sorted(dir(module)):
            if not attr.startswith("test_"):
                continue
            value = getattr(module, attr)
            if not callable(value) or isinstance(value, type):
                continue
            owner = getattr(value, "__qualname__", "")
            if "<locals>" in owner:
                continue
            function_count += 1
            try:
                value()
            except Exception:                               # noqa: BLE001
                function_failures.append((module.__name__, attr, traceback.format_exc()))

    for name, err in import_errors:
        print("\nIMPORT FAILURE %s\n%s" % (name, err))
    for mod, attr, err in function_failures:
        print("\nFUNCTION FAILURE %s.%s\n%s" % (mod, attr, err))

    total = result.testsRun + function_count
    failed = len(result.failures) + len(result.errors) + len(function_failures) + len(import_errors)
    print("\n%d checks run (%d unittest + %d module-level functions), %d failed"
          % (total, result.testsRun, function_count, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
