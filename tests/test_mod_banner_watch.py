#!/usr/bin/env python3
"""Offline checks for the banner watcher's injected script.

A syntax error in that script only surfaces after attaching to the running game, which is the most expensive
step in this lab. Rendering the template and parsing it costs a second. The parse uses node when present and
macOS's bundled JavaScriptCore otherwise.

No game and no Frida session needed: only the rendered text is inspected.
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
    import frida_mod_banner_watch as watch
except Exception:  # the module imports frida only inside main(), so this should succeed
    watch = None

NODE = shutil.which("node")
JSC = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc"
JSC = JSC if os.path.exists(JSC) else None


def js_syntax_error(text):
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


@unittest.skipUnless(watch is not None, "banner watcher unavailable")
class BannerWatcherScriptTest(unittest.TestCase):
    def test_every_placeholder_is_substituted(self):
        script = watch.build_script(90)
        self.assertNotIn("%X", script)
        self.assertNotIn("%d", script)
        self.assertIn("0x%X" % watch.RVA_SHOW_MOD, script)
        self.assertIn(str(90 * 1000), script)

    def test_offsets_reach_the_script(self):
        script = watch.build_script(1)
        self.assertIn("add(0x%X)" % watch.OFF_NAME, script)
        self.assertIn("add(0x%X)" % watch.OFF_DESC, script)

    @unittest.skipUnless(NODE or JSC, "no JavaScript engine to parse with")
    def test_the_script_parses_as_javascript(self):
        error = js_syntax_error(watch.build_script(60))
        self.assertIsNone(error, error)


if __name__ == "__main__":
    unittest.main()
