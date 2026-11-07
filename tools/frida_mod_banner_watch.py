#!/usr/bin/env python3
"""Watch the applied-mod banner and print the mod it is showing.

Why: the mod arrays (`ActiveModsRep`/`ActiveMods`/`QueuedModsRep`) read empty while a mod-like change is
visible, and both HUD surfaces render a catalogue mod's own name and description. The reading that fits both is
that a `GameplayMod` reaches the UI through an apply event without ever entering those arrays. This tool tests
that directly: it hooks the banner's `showMod` (`0x14056B4D0`) and prints the id/name it is handed, so a run can
say *which* mod the screen is showing at the same moment `frida_mod_poll.py` says the arrays are empty.

The payload is dereferenced the way the banner does: `[obj+0x18]` and `[obj+0x20]` are the component's name and
description slots. They are printed raw, plus resolved against the catalogue's FName ids where possible; the
exact slot semantics are not fully established, so treat an unresolved id as an id to look up, not as a name.

Usage: FRIDA_DEV=127.0.0.1:27042 python3 tools/frida_mod_banner_watch.py [seconds] [host|client]

Pair it with the array poll for the decisive observation:
    python3 tools/frida_mod_poll.py 240 host          # arrays, in another shell
    python3 tools/frida_mod_banner_watch.py 240 host  # banners, here
"""

import os
import sys
import time

DEV = os.environ.get("FRIDA_DEV", "127.0.0.1:27042")

# RVA_* values are offsets from the module base and are added to it in the script; a full VA here would
# double the base and attach outside the module.
RVA_SHOW_MOD = 0x56B4D0           # sl.ui.hud.AppliedModIndicator.showMod, VA 0x14056B4D0 (RVA, added to the module base)
# See frida_mod_truth.py: the accessor bodies put `description`/`shortDescription` at +0x18/+0x20 as integers,
# the name is a pointer at +0x00, and the catalogue-FName mapping is unverified — raw values are the datum.
OFF_NAME = 0x18
OFF_DESC = 0x20
RVA_FNAME_CTOR = 0x12DC9B0        # same helper frida_mod_poll.py uses to resolve FName ids

JS = r"""
setImmediate(function () {
  const m = Process.enumerateModules().find(x => x.name.toLowerCase().startsWith('streamline'));
  if (!m) { send({ev: 'error', why: 'Streamline module not found'}); return; }
  const base = m.base;

  const ctor = new NativeFunction(base.add(0x%X), 'pointer', ['pointer','pointer','int','int']);
  function fname(text){ const b = Memory.alloc(16); ctor(b, Memory.allocUtf16String(text), 1, 1); return b.readU32(); }
  const known = {};
  for (const n of ["game_mod_big_head_mode","game_mod_bonus_bonus","game_mod_concrete_shoes","game_mod_crabcore",
                   "game_mod_energy_drink","game_mod_floor_is_lava","game_mod_make_it_rain","game_mod_moon_boots",
                   "game_mod_multi_ball","game_mod_nerfed","game_mod_no_touching","game_mod_perpetual_motion",
                   "game_mod_pleb","game_mod_pogo","game_mod_points_b_gone","game_mod_puke_town",
                   "game_mod_ragdoll","game_mod_rise_from_your_graves","game_mod_sandstorm","game_mod_shady_business",
                   "game_mod_shrink_ray","game_mod_slo_thuggin","game_mod_spiritual_cleansing","game_mod_stop_drop_roll",
                   "game_mod_ugh_ice_level","game_mod_vengeful_spirits","game_mod_wolf_among_us","game_mod_xray_glasses",
                   "game_mod_yakety_hill"])
    known[fname(n)] = n;

  function safe(fn, dflt) { try { return fn(); } catch (e) { return dflt; } }

  const target = base.add(0x%X);
  Interceptor.attach(target, {
    onEnter: function (args) {
      const obj = args[1];                       // (this, modLikeObject)
      if (obj.isNull()) { send({ev: 'banner', null: true}); return; }
      const name = safe(function () { return obj.add(0x%X).readU32(); }, -1);
      const desc = safe(function () { return obj.add(0x%X).readU32(); }, -1);
      send({ev: 'banner', t: Date.now(),
            name_raw: name, name_id: known[name] || null,
            desc_raw: desc});
    }
  });

  send({ev: 'ready', hook: target.toString()});
  setTimeout(function () { send({ev: 'done'}); }, %d);
});
"""


def build_script(seconds):
    """The inline script, with every placeholder substituted (tests render and parse this)."""
    return JS % (RVA_FNAME_CTOR, RVA_SHOW_MOD, OFF_NAME, OFF_DESC, int(seconds) * 1000)


def main():
    import frida

    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 240
    device = frida.get_device_manager().add_remote_device(DEV)
    sessions = device.enumerate_processes()
    target = next((p for p in sessions
                   if "streamline" in p.name.lower()), None)
    if target is None:
        print("no Streamline process on %s (running: %s)"
              % (DEV, ", ".join(p.name for p in sessions) or "nothing"))
        return 2

    script = device.attach(target.pid).create_script(build_script(seconds))
    banners = []

    def on_message(message, data):
        if message.get("type") != "send":
            print("script error:", message)
            return
        payload = message.get("payload") or {}
        event = payload.get("ev")
        if event == "ready":
            print("hooked %s; watching for %ds" % (payload.get("hook"), seconds))
        elif event == "banner":
            if payload.get("null"):
                print("banner fired with a null payload")
                return
            name = payload.get("name_id") or "raw=%s" % payload.get("name_raw")
            print("%s  banner shows %s (desc raw=%s)"
                  % (time.strftime("%H:%M:%S"), name, payload.get("desc_raw")))
            banners.append(payload)
        elif event == "done":
            print("%d banner(s) seen" % len(banners))

    script.on("message", on_message)
    script.load()
    try:
        time.sleep(seconds)
    except KeyboardInterrupt:
        pass
    print("%d banner(s) recorded" % len(banners))
    return 0 if banners else 1


if __name__ == "__main__":
    sys.exit(main())
