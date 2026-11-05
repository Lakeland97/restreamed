#!/usr/bin/env python3
"""One attach that answers the open mod question: what the screen shows and what the arrays hold, together.

Two observations disagree. The three mod arrays (`ActiveModsRep`, `ActiveMods`, `QueuedModsRep`) measured empty
while a mod-like change was visibly running, yet both HUD surfaces that show a mod render a *catalogue* mod's own
name and description — and the banner's show path never looks the mod up in those arrays. The remaining reading is
that a `GameplayMod` reaches the screen through an apply event without entering the arrays.

Checking that needs both halves at one instant, which two separate tools make awkward. This does it in one hook
and one poll, and prints a verdict:

  banner seen, arrays non-zero   -> the ordinary path exists and the earlier poll missed it
  banner seen, arrays zero       -> the event route is real; the arrays are not the storage
  no banner, arrays moved        -> the arrays lead the screen; watch a longer window
  neither                        -> nothing to say; the run saw no mod

Usage: FRIDA_DEV=127.0.0.1:27042 python3 tools/frida_mod_truth.py [seconds] [host|client]

It is the instrument for the one observation that separates the two readings: the same instant, both sources.
"""

import os
import sys
import time

DEV = os.environ.get("FRIDA_DEV", "127.0.0.1:27042")

RVA_CLASS_CACHE = 0x3BBF640       # hx class cache for SLGameState (frida_mod_poll.py)
RVA_FNAME_CTOR = 0x12DC9B0        # FName constructor helper
RVA_SHOW_MOD = 0x56B4D0           # sl.ui.hud.AppliedModIndicator.showMod, VA 0x14056B4D0 (RVA, added to the module base)
OFF_NAME = 0x18                   # what the banner reads for its title
OFF_DESC = 0x20                   # ... and for the description line
ARRAY_OFFSETS = (0x480, 0x490, 0x4A0)

JS = r"""
setImmediate(function () {
  const m = Process.enumerateModules().find(x => x.name.toLowerCase().startsWith('streamline'));
  if (!m) { send({ev: 'error', why: 'Streamline module not found'}); return; }
  const base = m.base, end = base.add(m.size);

  const ctor = new NativeFunction(base.add(0x%X), 'pointer', ['pointer','pointer','int','int']);
  function fname(text) { const b = Memory.alloc(16); ctor(b, Memory.allocUtf16String(text), 1, 1); return b.readU32(); }
  const known = {};
  for (const n of ["game_mod_floor_is_lava","game_mod_ragdoll","game_mod_moon_boots","game_mod_energy_drink",
                   "game_mod_wolf_among_us","game_mod_big_head_mode","game_mod_pogo","game_mod_xray_glasses",
                   "game_mod_sandstorm","game_mod_make_it_rain","game_mod_crabcore","game_mod_pleb"])
    known[fname(n)] = n;

  let gameState = null;
  function findState() {
    if (gameState) { try { if (!gameState.readPointer().isNull()) return gameState; } catch (e) { gameState = null; } }
    const uclass = base.add(0x%X).readPointer();
    if (uclass.isNull()) return null;
    const pattern = uclass.toMatchPattern();
    for (const range of Process.enumerateRanges('rw-')) {
      if (range.size < 4096) continue;
      try {
        for (const hit of Memory.scanSync(range.base, range.size, pattern)) {
          const object = hit.address.sub(0x10);
          const vtable = object.readPointer();
          if (vtable.compare(base) < 0 || vtable.compare(end) >= 0) continue;
          if (object.add(0x1c).readU32() !== 0) continue;
          gameState = object; return object;
        }
      } catch (e) {}
    }
    return null;
  }

  let arraysEmpty = true;
  function readArrays() {
    const state = findState();
    if (!state) return null;
    const parts = [];
    let any = false;
    for (const offset of [0x480, 0x490, 0x4a0]) {
      let count = 0, first = -1;
      try {
        count = state.add(offset + 8).readU32();
        if (count > 0) {
          any = true;
          first = state.add(offset).readPointer().add(8).readU32();
        }
      } catch (e) {}
      parts.push(count + ':' + (known[first] || first));
    }
    arraysEmpty = !any;
    return parts.join('|');
  }

  Interceptor.attach(base.add(0x%X), {
    onEnter: function (args) {
      const payload = args[1];
      if (payload.isNull()) { send({ev: 'banner', null: true, arrays: readArrays()}); return; }
      let name = -1, desc = -1;
      try { name = payload.add(0x%X).readU32(); } catch (e) {}
      try { desc = payload.add(0x%X).readU32(); } catch (e) {}
      send({ev: 'banner', t: Date.now(), name_raw: name, name_id: known[name] || null,
            desc_raw: desc, arrays: readArrays(), arrays_empty: arraysEmpty});
    }
  });

  let last = null;
  setInterval(function () {
    const signature = readArrays();
    if (signature !== null && signature !== last) {
      last = signature;
      send({ev: 'arrays', t: Date.now(), sig: signature});
    }
  }, 1000);

  send({ev: 'ready', hook: base.add(0x%X).toString()});
  setTimeout(function () { send({ev: 'done'}); }, %d);
});
"""


def build_script(seconds):
    """Render the inline script; every placeholder must be substituted (the parse test checks this)."""
    return JS % (RVA_FNAME_CTOR, RVA_CLASS_CACHE, RVA_SHOW_MOD, OFF_NAME, OFF_DESC,
                 RVA_SHOW_MOD, int(seconds) * 1000)


def verdict(state):
    """Turn the recorded events into one sentence."""
    banners = state["banners"]
    moves = state["moves"]
    if banners and any(banner.get("arrays_empty") for banner in banners):
        return ("banner seen while the arrays read empty — the apply event carries a mod the arrays do not hold")
    if banners and moves:
        return "banner and array movement both seen — the ordinary path exists; re-time the earlier poll"
    if moves:
        return "arrays moved but no banner was captured — watch a longer window"
    if banners:
        return "banner seen, array state unknown (no poll completed) — rerun with the game state resolvable"
    return "neither a banner nor an array change — the run saw no mod"


def main():
    import frida

    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 240
    device = frida.get_device_manager().add_remote_device(DEV)
    target = next((p for p in device.enumerate_processes() if "streamline" in p.name.lower()), None)
    if target is None:
        print("no Streamline process on %s" % DEV)
        return 2

    script = device.attach(target.pid).create_script(build_script(seconds))
    state = {"banners": [], "moves": []}

    def on_message(message, data):
        if message.get("type") != "send":
            print("script error:", message)
            return
        payload = message.get("payload") or {}
        event = payload.get("ev")
        if event == "ready":
            print("hooked %s; watching for %ds" % (payload.get("hook"), seconds))
        elif event == "banner":
            state["banners"].append(payload)
            name = payload.get("name_id") or "raw=%s" % payload.get("name_raw")
            print("%s  BANNER %s  arrays=%s%s"
                  % (time.strftime("%H:%M:%S"), name, payload.get("arrays"),
                     "  (empty)" if payload.get("arrays_empty") else ""))
        elif event == "arrays":
            state["moves"].append(payload)
            print("%s  arrays %s" % (time.strftime("%H:%M:%S"), payload.get("sig")))
        elif event == "done":
            print("window closed")

    script.on("message", on_message)
    script.load()
    try:
        time.sleep(seconds)
    except KeyboardInterrupt:
        pass
    print("%d banner(s), %d array change(s)" % (len(state["banners"]), len(state["moves"])))
    print("verdict:", verdict(state))
    return 0


if __name__ == "__main__":
    sys.exit(main())
