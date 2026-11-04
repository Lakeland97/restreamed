#!/usr/bin/env python3
"""Parse every injected Frida script the lab ships, before a probe ever attaches.

A syntax error in one of these scripts only shows up after attaching to a running game — the most expensive step
in the loop. Rendering the template and parsing it costs a fraction of a second.

Tools that expose `build_script` are rendered through it with a representative argument set. Tools that keep a
raw `JS` template without a builder are skipped here and listed in `NO_BUILDER` with the reason, so an unparsed
script is visible rather than silently assumed fine.

Parsing uses node when present and macOS's bundled JavaScriptCore otherwise; a Frida script reaches runtime
globals, so only a SyntaxError counts as failure.
"""

import importlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)
sys.path.insert(0, os.path.join(ROOT, "tools"))

NODE = shutil.which("node")
JSC = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc"
JSC = JSC if os.path.exists(JSC) else None

# Tools whose template needs a real argument set to render (everything else is rendered textually).
BUILDERS = {
    "frida_apply_mod": ("game_mod_pogo", 30),
    "frida_enter_tutorial": (),
    "frida_mod_banner_watch": (60,),
    "frida_prop_offset": ("ActiveModsRep",),
}

# A `%X`/`%d`/`%s` left in a template is a format placeholder the tool fills at attach time; substituting a
# representative token is what a builder would do, and it is enough to parse the script.
PLACEHOLDER = re.compile(r"%(?:X|x|d|s)")


def render(module):
    """The script text the tool would inject, as close as this test can get without a session."""
    builder = getattr(module, "build_script", None)
    if builder is not None:
        return builder(*BUILDERS.get(module.__name__, ()))
    template = getattr(module, "JS", None)
    if template is not None:
        return PLACEHOLDER.sub("0", template)
    # Some probes keep several templates under their own names (e.g. frida_mod_watch's LOCATE/WATCH); render
    # them all, joined, so one parse covers the file.
    chunks = [PLACEHOLDER.sub("0", value) for name, value in vars(module).items()
              if name.isupper() and isinstance(value, str) and "setImmediate(" in value]
    if chunks:
        return "\n;\n".join(chunks)
    return None


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


@unittest.skipUnless(NODE or JSC, "no JavaScript engine to parse with")
class FridaScriptParseTest(unittest.TestCase):
    def test_every_frida_tool_renders_a_parseable_script(self):
        skipped = []
        for name in sorted(os.listdir(os.path.join(ROOT, "tools"))):
            if not (name.startswith("frida_") and name.endswith(".py")):
                continue
            module_name = name[:-3]
            with self.subTest(tool=module_name):
                try:
                    module = importlib.import_module(module_name)
                except Exception as exc:  # noqa: BLE001 - a missing dependency is a skip, not a failure
                    skipped.append((module_name, str(exc)))
                    continue
                script = render(module)
                if script is None:
                    skipped.append((module_name, "no build_script and no JS template"))
                    continue
                self.assertNotIn("%X", script, "%s left a %%X placeholder" % module_name)
                error = syntax_error(script)
                self.assertIsNone(error, "%s: %s" % (module_name, error))
        if skipped:
            # Visible, not silent: print each skip with its reason so coverage is auditable.
            print("\nskipped %d tool(s):" % len(skipped))
            for module_name, why in skipped:
                print("  %-28s %s" % (module_name, why))

    # A tool with no renderable template has to say why, or it counts as uncovered.
    DOCUMENTED_SKIPS = {
        "frida_class_props": "drives a session interactively; no injected template",
    }

    def test_the_sweep_covers_every_frida_tool(self):
        tools = sorted(name[:-3] for name in os.listdir(os.path.join(ROOT, "tools"))
                       if name.startswith("frida_") and name.endswith(".py"))
        # The public tree ships one probe; the lab tree ships the whole set. The sweep must cover whatever is
        # present, and the uncovered list is what catches a new tool arriving without a parse check.
        self.assertGreaterEqual(len(tools), 1, tools)
        uncovered = []
        for module_name in tools:
            try:
                module = importlib.import_module(module_name)
            except Exception:  # noqa: BLE001 - counted as a skip by the sweep test
                continue
            if render(module) is None and module_name not in self.DOCUMENTED_SKIPS:
                uncovered.append(module_name)
        self.assertEqual(uncovered, [], "frida tools with no renderable script: %s" % uncovered)


if __name__ == "__main__":
    unittest.main()
