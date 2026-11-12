#!/usr/bin/env python3
"""Offline checks for the combined mod-truth probe.

The script only runs after attaching to a live match, so a syntax error or an unsubstituted placeholder would
cost a probe cycle. Both are checked here, along with the verdict logic — the part that decides what the
observation means, and therefore the part that must not be ambiguous.

No game and no Frida session: the template is rendered and parsed, and the verdict function is exercised on
synthetic event sets.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

try:
    import frida_mod_truth as truth
except Exception:  # the module imports frida only inside main()
    truth = None

NODE = shutil.which("node")
JSC = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc"
JSC = JSC if os.path.exists(JSC) else None


def syntax_error(text):
    handle, path = tempfile.mkstemp(suffix=".js")
    try:
        with os.fdopen(handle, "w") as fh:
            fh.write(text)
        if NODE:
            result = subprocess.run([NODE, "--check", path], capture_output=True, text=True)
            return None if result.returncode == 0 else result.stderr
        if JSC:
            result = subprocess.run([JSC, path], capture_output=True, text=True)
            blob = (result.stderr or "") + (result.stdout or "")
            return blob if "SyntaxError" in blob else None
        return None
    finally:
        os.unlink(path)


@unittest.skipUnless(truth is not None, "mod-truth probe unavailable")
class ArgumentTest(unittest.TestCase):
    def test_only_the_window_is_accepted(self):
        self.assertEqual(truth.parse_args(["tool"]), (240, None))
        self.assertEqual(truth.parse_args(["tool", "60"]), (60, None))
        seconds, error = truth.parse_args(["tool", "60", "host"])
        self.assertIsNone(seconds)
        self.assertIn("usage", error)

    def test_a_non_numeric_window_is_rejected(self):
        seconds, error = truth.parse_args(["tool", "soon"])
        self.assertIsNone(seconds)
        self.assertIn("integer", error)


class ModTruthTest(unittest.TestCase):
    def test_every_placeholder_is_substituted(self):
        script = truth.build_script(120)
        self.assertNotIn("%X", script)
        self.assertNotIn("%d", script)
        self.assertIn("0x%X" % truth.RVA_SHOW_MOD, script)
        self.assertIn(str(120 * 1000), script)
        for offset in (truth.OFF_NAME, truth.OFF_DESC):
            self.assertIn("add(0x%X)" % offset, script)
        # The array offsets are written in lower case inside the JS list literal; compare case-insensitively.
        for offset in truth.ARRAY_OFFSETS:
            self.assertIn(("0x%X" % offset).lower(), script.lower())

    @unittest.skipUnless(NODE or JSC, "no JavaScript engine to parse with")
    def test_the_script_parses_as_javascript(self):
        error = syntax_error(truth.build_script(60))
        self.assertIsNone(error, error)

    def test_the_verdict_prefers_the_event_reading(self):
        state = {"banners": [{"arrays_empty": True, "name_id": "game_mod_pogo"}], "moves": []}
        self.assertIn("apply event", truth.verdict(state))

    def test_the_verdict_reports_the_ordinary_path_when_the_arrays_moved(self):
        state = {"banners": [{"arrays_empty": False}], "moves": [{"sig": "1:x"}]}
        self.assertIn("ordinary path", truth.verdict(state))

    def test_the_verdict_is_honest_when_nothing_was_seen(self):
        self.assertIn("saw no mod", truth.verdict({"banners": [], "moves": []}))
        self.assertIn("no banner", truth.verdict({"banners": [], "moves": [{"sig": "1:x"}]}))

    def test_the_verdict_says_so_when_the_array_state_is_unknown(self):
        state = {"banners": [{"arrays_empty": None}], "moves": []}
        self.assertIn("unknown", truth.verdict(state))


if __name__ == "__main__":
    unittest.main()
