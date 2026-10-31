#!/usr/bin/env python3
"""Guard: published code must not carry lab-internal references.

The project publishes only project material. Report names, round ids, absolute paths and
harness wording do not belong in shipped tools or tests, and they are easy to reintroduce in a
docstring or an assert message. This test scans the published sources and fails if any reappear,
so the CI run catches it on the push that introduces it.

Self-reference note: the patterns below are assembled from fragments so this file does not match
its own scan if it is ever included.
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SELF = os.path.abspath(__file__)

BANNED = [
    (re.compile("/Users/"), "absolute user path"),
    (re.compile("run" "time/"), "lab report directory"),
    (re.compile("MOD" "LOG"), "lab round log"),
    (re.compile(r"\bR\d{3,4}\b"), "internal round reference"),
    (re.compile("in-" "guest"), "environment wording"),
    (re.compile(r"\bno (?:client, )?game,? VM\b", re.I), "environment wording"),
]


def sources():
    for sub in ("tools", "tests"):
        base = os.path.join(ROOT, sub)
        for dirpath, _dirs, files in os.walk(base):
            for name in files:
                if not name.endswith((".py", ".sh", ".cpp", ".h")):
                    continue
                path = os.path.join(dirpath, name)
                if os.path.abspath(path) == SELF:
                    continue
                yield path


class NoInternalReferencesTest(unittest.TestCase):
    def test_published_sources_carry_no_lab_references(self):
        hits = []
        for path in sources():
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                for number, line in enumerate(handle, 1):
                    for pattern, why in BANNED:
                        if pattern.search(line):
                            hits.append("%s:%d: %s | %s"
                                        % (os.path.relpath(path, ROOT), number, why,
                                           line.strip()[:100]))
                            break
        self.assertEqual(hits, [], "lab-internal material in published sources:\n" +
                         "\n".join(hits))


if __name__ == "__main__":
    unittest.main(verbosity=2)
