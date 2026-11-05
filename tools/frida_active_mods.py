"""Find the live game state, resolve `ActiveMods` by name, and print its offset.

Stage 1 scans memory for the ASLGameState UClass (read from the class registration storage at
`0x3AC10A8`). Stage 2, given an instance, calls the engine's own `FindPropertyByName`
(`0x14012BB50`) with an `FName` built by `FName::FName(const TCHAR*)` (`0x1412DC9B0`), then reads
`Offset_Internal` at `+0x4C`.

Read-only: nothing is written to the process. Run against a client that is inside a match (HOME has no
game state).
"""
import os
import sys
import time

import frida

DEV = os.environ.get("FRIDA_DEV", "192.168.64.3:27042")
# Module-relative addresses. The script adds these to the module base, so a full VA here would double the base
# and attach outside the image (the bug fixed here: both call targets were stored as VAs).
CLASS_STORAGE = 0x3AC10A8          # VA 0x143AC10A8
FIND_PROPERTY = 0x12BB50           # VA 0x14012BB50, ASLObject::FindPropertyByName
FNAME_CTOR = 0x12DC9B0             # VA 0x1412DC9B0, FName::FName(const TCHAR*)

JS = r"""
const mod = Process.enumerateModules().find(m => m.name.toLowerCase().startsWith('streamline'));
const base = mod.base;
const storage = base.add(0x%X);
const classPtr = storage;   // the static UClass object itself
send({ev: 'class', class: classPtr.toString()});

const findProperty = new NativeFunction(base.add(0x%X), 'pointer', ['pointer', 'pointer']);
const fnameCtor = new NativeFunction(base.add(0x%X), 'void', ['pointer', 'pointer']);

function resolve(ufield, name) {
  const wide = Memory.allocUtf16String(name);
  const fname = Memory.alloc(8);
  fnameCtor(fname, wide);                 // FName(const TCHAR*)
  const prop = findProperty(ufield, fname);
  if (prop.isNull()) return null;
  return {prop: prop.toString(), offset: prop.add(0x4C).readU32()};
}

setImmediate(function () {
  const pat = classPtr.toMatchPattern();
  send({ev: 'scan_start', ranges: Process.enumerateRanges('rw-').length});
  const hits = [];
  let scanned = 0;
  for (const r of Process.enumerateRanges('rw-')) {
    if (r.size < 1024 * 1024) continue;            // actors live in large blocks
    if (r.size > 512 * 1024 * 1024) continue;
    try {
      for (const f of Memory.scanSync(r.base, r.size, pat)) hits.push(f.address);
    } catch (e) {}
    scanned += r.size;
    if (hits.length > 400) break;
  }
  send({ev: 'scan_done', hits: hits.length, scanned: scanned});
  const objs = [];
  for (const h of hits) {
    try {
      const obj = h.sub(0x10);
      const vt = obj.readPointer();
      if (vt.compare(base) >= 0 && vt.compare(base.add(mod.size)) < 0) objs.push(obj);
    } catch (e) {}
  }
  send({ev: 'instances', count: objs.length});
  if (!objs.length) return;
  const obj = objs[0];
  // UObject->ClassPrivate is the UClass; its Children list holds the properties.
  const cls = obj.add(0x10).readPointer();
  const prop = resolve(cls, 'ActiveMods');
  send({ev: 'ActiveMods', obj: obj.toString(), prop: prop});
  if (prop) {
    try {
      const arr = obj.add(prop.offset);
      send({ev: 'array', data: arr.readPointer().toString(), num: arr.add(8).readS32(),
            max: arr.add(0xC).readS32()});
    } catch (e) { send({ev: 'array_error', e: String(e)}); }
  }
});
"""


def main():
    dev = frida.get_device_manager().add_remote_device(DEV)
    target = next((p for p in dev.enumerate_processes() if p.name.lower().startswith("streamline")), None)
    if not target:
        print("no Streamline process")
        return 1
    session = None
    for attempt in range(4):
        try:
            session = dev.attach(target.pid)
            break
        except Exception as exc:
            print("attach attempt %d failed: %s" % (attempt + 1, exc))
            time.sleep(5)
    if session is None:
        print("could not attach")
        return 1
    script = session.create_script(JS % (CLASS_STORAGE, FIND_PROPERTY, FNAME_CTOR))

    def on_message(message, data):
        if message.get("type") == "send":
            print(message["payload"])
        else:
            print("error:", message)

    script.on("message", on_message)
    script.load()
    time.sleep(45)
    session.detach()
    return 0


if __name__ == "__main__":
    sys.exit(main())
