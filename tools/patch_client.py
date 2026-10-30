#!/usr/bin/env python3
"""Turn your own Streamline.exe into the patched host/client builds.

This is a clean-room patcher: it holds no game code. The table below carries only
the handful of original bytes it verifies at each site, and the small stubs this
project injects. Everything else comes from the copy of the game you already own.

What the patch set does (see the wiki for the analysis behind each site):

  * login/admission byte patches (H1-H9, K1, A1): a listen host cannot complete the
    appserver login handshake, so the gates that wait for it are bypassed and a
    joining client is admitted without a game-session id.
  * identity cave: a player built from a URL has no appserver accountID, so the
    contestant record key is empty for everybody. The cave copies playerSessionID
    (or the player name) into playerIID and mirrors it into the UE SLPlayerState,
    so the producer key, the consumer key and the serialized record all agree.
  * post-match loop cave: after MatchOver the host returns to WaitingForPlayers
    instead of sitting on the awards screen.
  * round timer cave: an in-round phase never ended on its own because the
    m_timeLimitReached latch had no reader. The cave keeps the latch and then
    calls gotoPhase("RoundEnding").

The three caves live in the 260 zero bytes that .rdata already has past its
VirtualSize. The patch set therefore also raises .rdata VirtualSize to the
section's SizeOfRawData and sets MEM_EXECUTE so the caves are mapped and
executable.

Every patch changes the executable, so the PE optional-header checksum is
recomputed after patching (tools/fix_pe_checksum.py) to keep the binary
self-consistent. It is not a DRM gate: the identity check is launcher-based, and
the shipped binary's own stored checksum is already stale.

Profiles
--------
  host      the full working host set: identity cave (hook + stub + phase),
            post-match loop cave, round timer cave, the host byte patches and the
            match-data patches. This reproduces the build the project tested.
  host-min  the same byte patches without any cave: no .rdata mapping, no stubs.
            Useful for isolating the caves from the byte-level edits.
  list      print the patch table (site, VA, file offset, purpose) and exit.

Usage
-----
  python3 tools/patch_client.py --profile host --in Streamline.exe --out Streamline.host.exe
  python3 tools/patch_client.py --profile host --verify Streamline.host.exe
  python3 tools/patch_client.py --profile list

  --add NAME / --drop NAME override single sites on a profile (repeatable), e.g.
  to rebuild the earlier non-cave-timer host:
      --profile host --drop round_timer_hook --drop round_timer_cave --add round_end_le2

The patcher refuses to write anything unless every original byte in the selected
profile still matches, and it will not overwrite its own input: patch a copy.
"""
import argparse
import hashlib
import os
import struct
import sys

try:
    from fix_pe_checksum import checksum_offset, compute_checksum
except ImportError:                                     # imported as tools.patch_client
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from fix_pe_checksum import checksum_offset, compute_checksum


# The section table is the only authority for VA <-> file offset (the shipped
# client's .text is VA = file + 0x140000C00, its .rdata VA = file + 0x140000E00,
# and both are re-derived from the PE header on every run).

# The shipped .rdata tail: VirtualSize 0x00DF5AFC, SizeOfRawData 0x00DF5C00, so
# 260 zero bytes follow the mapped data. Every cave body below fits in them, and
# mapping them (VirtualSize -> SizeOfRawData, MEM_EXECUTE) is the only header
# edit a cave profile needs. RDATA_VIRTUALSIZE_SHIPPED doubles as the build
# identity check: any other value means this is not the supported client.
RDATA_VIRTUALSIZE_SHIPPED = 0x00DF5AFC
RDATA_VIRTUALSIZE = 0x00DF5C00
MEM_EXECUTE = 0x20000000


class PatchError(Exception):
    """A site did not match, the table is inconsistent, or a profile is invalid."""


class Site(object):
    """One length-preserving byte replacement at a known VA / file offset."""

    __slots__ = ("name", "va", "offset", "original", "patched", "purpose")

    def __init__(self, name, va, offset, original, patched, purpose):
        self.name = name
        self.va = va
        self.offset = offset
        self.original = original
        self.patched = patched
        self.purpose = purpose

    @property
    def size(self):
        return len(self.original)

    def describe(self):
        return "%-26s @%#010x (%#010x) %3dB  %s -> %s" % (
            self.name, self.offset, self.va, self.size,
            self.original.hex(), self.patched.hex(),
        )

    def __repr__(self):
        return "<Site %s @%#x>" % (self.name, self.offset)


def _s(name, va, offset, original, patched, purpose):
    """Table row. `original`/`patched` are hex strings or byte strings."""
    def raw(value):
        return bytes.fromhex(value) if isinstance(value, str) else bytes(value)
    old, new = raw(original), raw(patched)
    if len(old) != len(new):
        raise PatchError("%s: original is %d bytes, patch is %d"
                         % (name, len(old), len(new)))
    if not old:
        raise PatchError("%s: empty patch" % name)
    return Site(name, va, offset, old, new, purpose)


# --- the patch table ---------------------------------------------------------
# Order is the order of the lab build history. VA is authoritative (the file
# offset is derived from the PE section table and cross-checked on every run).
# The wiki ids (H1..H9, K1, A1) are the labels used by the analysis pages.

SITES = (
    # --- host byte patches: login and admission ------------------------------
    _s("login_startplay_gate", 0x1407B2A2E, 0x007B1E2E, "84c0", "31c0",
       "H1: StartPlay accepts the Hosted variant off-dedicated (a listen world is NM_ListenServer)"),
    _s("login_retain", 0x141700822, 0x016FFC22, "7f95", "9090",
       "H2: keep the controller AGameMode::Login already spawned"),
    _s("activate_server_ret", 0x140D48650, 0x00D47A50, "40", "c3",
       "K1: LoggedInPlayer.startLogin returns at entry (no +8=1, no loadPlayerFromAppServer)"),
    _s("startlogin_skip", 0x1407B0EE3, 0x007B02E3, "84c0", "31c0",
       "H7: bypass 'Setting player defaults with no playerIID' and the self-kick"),
    _s("set_player_defaults_skip", 0x140B7E4F0, 0x00B7D8F0, "48895c2410", "c390909090",
       "A1: DedicatedServerManager.activateServer returns at entry"),
    _s("register_server_skip", 0x1407B2DAF, 0x007B21AF, "84c0", "0c01",
       "H6: skip registerServer (it calls the private proxy with no null test)"),
    _s("prelogin_nohost", 0x1407BB52D, 0x007BA92D, "84c0", "31c0",
       "H3: joiner admission bypasses 'No game session ID was provided'"),
    _s("prelogin_session", 0x1407BBDC9, 0x007BB1C9, "84c0", "31c0",
       "H4: joiner admission bypasses 'No host is currently on the server.'"),
    _s("forced_registered", 0x1407BA713, 0x007B9B13, "0f94c3", "30db90",
       "H5: joiner admission bypasses 'Server is initializing' (m_registered == 0)"),
    _s("inactivity_kick_ret", 0x140CC61C0, 0x00CC55C0, "40", "c3",
       "H8: WaitingForPlayersPhase.onTimeout never kicks the host"),
    _s("gate5b_nop", 0x140719440, 0x00718840, "741b", "9090",
       "gate 5b NOP: skip the unconditional 0x5B gate on the way into the match"),
    _s("matchover_s2", 0x140CC0172, 0x00CBF572, "ff9080020000", "909090909090",
       "skip the MatchOver rewards RPC (onEndMatch on the private proxy)"),
    _s("wait_two", 0x1407B73C8, 0x007B67C8,
       "85c00f8e10010000488b5c2478488b7c2478660f1f440000",
       "83f8020f8c25010000e90a010000" + "90" * 10,
       "require two players before the match starts (cmp eax,2 / jl), replacing 'at least one'"),

    # --- match data ----------------------------------------------------------
    _s("pawn_restart_allow", 0x1407D5833, 0x007D4C33, "7514", "9090",
       "H9: SLPlayerController.CanRestartPlayer returns true with an empty appserver container"),
    _s("send_match_data_bypass", 0x140B957EE, 0x00B94BEE, "75", "eb",
       "force sendMatchData past the config key"),
    _s("send_match_data_post_skip", 0x140B96864, 0x00B95C64, "e817c6d5ff", "9090909090",
       "skip the onEndMatch POST (null private proxy)"),

    # --- identity cave -------------------------------------------------------
    _s("contestants_phase", 0x140B80E9E, 0x00B8029E, "26", "56",
       "rebuild the contestants list at StartingRound instead of WaitingForPlayers"),
    _s("identity_hook", 0x140D46E30, 0x00D46230, "48895c2410", "e92b1dd702",
       "hook LoggedInPlayer.initializePlayerState into the identity cave"),
    _s("identity_cave", 0x143AB8B60, 0x03AB7D60, bytes(125),
       "51524883b9800000000075654c8b89c00000008b81b800000085c090750c4c8b49"
       "188b411085c09074478981880000004c8989900000004883ec58894424204c894c"
       "24284889542450488d4c2430488d542420e8d840defc4c8b00488b4c2450488b09"
       "488d542440e8331dd3fc4883c4585a5948895c2410e958e228fd",
       "for a record with no accountID, set playerIID from playerSessionID (else the name) "
       "and mirror it into SLPlayerState"),

    # --- post-match loop cave ------------------------------------------------
    _s("loop3_hook", 0x140CC01C8, 0x00CBF5C8, "0f84cf050000", "e92f89df0290",
       "hook the MatchOver rewardsComplete branch into the loop cave"),
    _s("loop3_cave", 0x143AB8AFC, 0x03AB7CFC, bytes(31),
       "488b4e284885c97411488b15bcd410004885d27405e8faebb5fce9827c20fd",
       "gotoPhase('WaitingForPlayers') after MatchOver, then rejoin the epilogue"),

    # --- round timer cave ----------------------------------------------------
    _s("round_timer_hook", 0x140CBE1B0, 0x00CBD5B0, "c6413001c3", "e928aadf02",
       "hook the phase clock's +0x108 expiry handler (shared by In/Elim/Tag round phases)"),
    _s("round_timer_cave", 0x143AB8BDD, 0x03AB7DDD, bytes(23),
       "c6413001488b4128488b154cd510004889c1e91cebb5fc",
       "keep the m_timeLimitReached latch, then gotoPhase('RoundEnding')"),


    # --- client profile: DRM identity ---------------------------------------
    # The client must pass the launcher check before it can log in. The cave
    # builds DRMType.FakeID("restrea1") and hands it to getDRM's success path.
    # It lives in the dead padding at the end of .text, so no header edit.
    _s("drm_cave", 0x142CC2D95, 0x02CC2195,
       "4883ec28488d0d13000000e89b566bfe4883c4284055534156e9e2589afd470055004c003a0044000d000a000000524883ec20488d0d14000000e86c566bfe4883c4205a488bc45556e9724953fe470055004c003a0043000d000a00000000000000000000000000",
       "4883ec40c744242008000000c744242400000000488d0d4500000048894c242848c744243000000000488d542420488d4c2430488d05f14205feffd0488b4c243048894c2430488d542430488bcf488d051691c2fdffd04883c440e97972aefd7265737472656131",
       "DRM cave: construct FakeID(restrea1) and return it through getDRM"),
    _s("drm_entry_a", 0x1407AA07E, 0x007A947E, "488d0583ee", "e9128d5102",
       "hook getDRM's no-identity path into the DRM cave"),
    _s("drm_entry_b", 0x1407AA002, 0x007A9402, "488d05ffee", "e98e8d5102",
       "hook getDRM's Steam-identity path into the DRM cave"),
    _s("drm_entry_c", 0x1407AA09B, 0x007A949B, "4c8d45f048", "e9f58c5102",
       "hook getDRM's shared error path into the DRM cave"),

    _s("cosmetic_clear_skip", 0x1405E7BBF, 0x005E6FBF, "e8bcd54a00", "9090909090",
       "skip the per-slot SetSkeletalMesh(null) that removes a mesh the cosmetics block just set"),

    # --- HOME model -----------------------------------------------------------
    _s("mesh_gate_force", 0x1405EA31C, 0x005E971C, "7463", "eb63",
       "always enter the cosmetics mesh/AnimGraph block, even when the gender is unchanged"),
    _s("mesh_gate_nop", 0x1405EA37B, 0x005E977B, "0f85a7000000", "909090909090",
       "alternative: drop the post-block early exit (same effect, 6 bytes)"),

    # --- optional sites, not part of a profile -------------------------------
    _s("matchover_m2", 0x140CC01BE, 0x00CBF5BE, "0f84d9050000", "909090909090",
       "drop the MatchOver +0x31 early exit; loop3 keeps it as the clock gate"),
    _s("p3_force_loaded", 0x1407B7320, 0x007B6720, "40535556", "b001c390",
       "force the 'all players loaded' predicate (inert while wait_two is applied)"),
    _s("score_serialize_one", 0x1407EA9AD, 0x007E9DAD, "e87e44aeff", "b801000000",
       "serialize exactly one score element"),
    _s("round_end_le2", 0x140CCF3F2, 0x00CCE7F2, "01", "02",
       "end the round at <=2 contestants (immediate round end; conflicts with round_timer)"),
)

SITE_BY_NAME = {site.name: site for site in SITES}
SITE_ORDER = {site.name: index for index, site in enumerate(SITES)}

# Stub bodies live in the .rdata tail; a profile that includes one must map that
# tail executable and must include its hook.
CAVE_BODIES = ("identity_cave", "loop3_cave", "round_timer_cave")
CAVE_GROUPS = (
    ("identity cave", ("identity_hook", "identity_cave", "contestants_phase")),
    ("post-match loop cave", ("loop3_hook", "loop3_cave")),
    ("round timer cave", ("round_timer_hook", "round_timer_cave")),
)

HOST_BYTES = (
    "login_startplay_gate", "login_retain", "activate_server_ret", "startlogin_skip",
    "set_player_defaults_skip", "register_server_skip", "prelogin_nohost",
    "prelogin_session", "forced_registered", "inactivity_kick_ret", "gate5b_nop",
    "matchover_s2", "wait_two", "pawn_restart_allow", "send_match_data_bypass",
    "send_match_data_post_skip",
)

CAVE_SITES = tuple(name for group in CAVE_GROUPS for name in group[1])


class Profile(object):
    __slots__ = ("name", "description", "sites", "maps_rdata_exec")

    def __init__(self, name, description, site_names, maps_rdata_exec):
        self.name = name
        self.description = description
        # always in table order, whatever order the groups were listed in
        self.sites = tuple(sorted((SITE_BY_NAME[n] for n in site_names),
                                  key=lambda site: SITE_ORDER[site.name]))
        self.maps_rdata_exec = maps_rdata_exec

    @property
    def site_names(self):
        return tuple(site.name for site in self.sites)

    def __repr__(self):
        return "<Profile %s (%d sites)>" % (self.name, len(self.sites))


CLIENT_SITES = ("drm_cave", "drm_entry_a", "drm_entry_b", "drm_entry_c")

PROFILES = {
    "client": Profile(
        "client",
        "DRM identity cave only: lets the stock login flow pass the launcher check",
        CLIENT_SITES,
        False,
    ),
    "home-model": Profile(
        "home-model",
        "client DRM cave plus the cosmetics fix: the HOME player model is dressed with the default set",
        CLIENT_SITES + ("cosmetic_clear_skip",),
        False,
    ),
    "host": Profile(
        "host",
        "full working host set: identity cave, post-match loop cave, round timer cave, "
        "host byte patches, match-data patches",
        HOST_BYTES + CAVE_SITES,
        True,
    ),
    "host-min": Profile(
        "host-min",
        "host byte patches only: no caves, no .rdata mapping",
        HOST_BYTES,
        False,
    ),
}


# --- PE plumbing -------------------------------------------------------------

def _u16(data, offset):
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def _u64(data, offset):
    return struct.unpack_from("<Q", data, offset)[0]


class Section(object):
    __slots__ = ("name", "va", "vsize", "rawptr", "rawsize", "header")

    def __init__(self, name, va, vsize, rawptr, rawsize, header):
        self.name = name
        self.va = va
        self.vsize = vsize
        self.rawptr = rawptr
        self.rawsize = rawsize
        self.header = header

    def contains_file(self, offset):
        return self.rawptr <= offset < self.rawptr + self.rawsize

    def __repr__(self):
        return "<Section %s va=%#x raw=%#x+%#x>" % (
            self.name, self.va, self.rawptr, self.rawsize)


def sections(data):
    """Parse the section table. Raises PatchError on anything that is not a PE."""
    if len(data) < 0x40 or data[:2] != b"MZ":
        raise PatchError("not a PE image (no MZ header)")
    pe = _u32(data, 0x3C)
    if data[pe:pe + 4] != b"PE\0\0":
        raise PatchError("not a PE image (no PE signature at %#x)" % pe)
    count = _u16(data, pe + 6)
    opt_size = _u16(data, pe + 20)
    opt = pe + 24
    base = _u64(data, opt + 24)
    table = opt + opt_size
    out = []
    for i in range(count):
        header = table + i * 40
        if header + 40 > len(data):
            raise PatchError("truncated section table")
        name = data[header:header + 8].rstrip(b"\0").decode("latin1")
        vsize, vaddr, rawsize, rawptr = struct.unpack_from("<IIII", data, header + 8)
        out.append(Section(name, base + vaddr, vsize, rawptr, rawsize, header))
    if not out:
        raise PatchError("PE image has no sections")
    return out


def section_for_file_offset(secs, offset):
    for sec in secs:
        if sec.contains_file(offset):
            return sec
    raise PatchError("file offset %#x is not in any section" % offset)


def file_to_va(secs, offset):
    sec = section_for_file_offset(secs, offset)
    return sec.va + (offset - sec.rawptr)


def _rdata(secs):
    for sec in secs:
        if sec.name == ".rdata":
            return sec
    raise PatchError("PE image has no .rdata section")


# --- table validation --------------------------------------------------------

def validate_table(sites):
    """Structural check of a patch table: unique names, sizes, no overlap."""
    seen = {}
    for site in sites:
        if site.name in seen:
            raise PatchError("duplicate site name %r" % site.name)
        if site.size != len(site.patched):
            raise PatchError("%s: original and patch differ in size" % site.name)
        if site.size == 0:
            raise PatchError("%s: empty patch" % site.name)
        if site.va <= site.offset:
            raise PatchError("%s: VA %#x cannot be below file offset %#x "
                             "(swapped fields?)" % (site.name, site.va, site.offset))
        for other in seen.values():
            if site.offset < other.offset + other.size and other.offset < site.offset + site.size:
                raise PatchError("sites %s and %s overlap at %#x"
                                 % (other.name, site.name, site.offset))
        seen[site.name] = site
    return True


def validate_geometry(data, sites):
    """Check every site against this image: VA matches, caves fit the .rdata tail."""
    secs = sections(data)
    rdata = _rdata(secs)
    has_caves = any(site.name in CAVE_BODIES for site in sites)
    if has_caves and rdata.vsize not in (RDATA_VIRTUALSIZE_SHIPPED, RDATA_VIRTUALSIZE):
        raise PatchError(
            ".rdata VirtualSize is %#x, expected %#x (shipped) or %#x (mapped); "
            "this is not the supported client build"
            % (rdata.vsize, RDATA_VIRTUALSIZE_SHIPPED, RDATA_VIRTUALSIZE))
    cave_low = rdata.rawptr + RDATA_VIRTUALSIZE_SHIPPED
    cave_high = rdata.rawptr + RDATA_VIRTUALSIZE
    for site in sites:
        if site.offset + site.size > len(data):
            raise PatchError("%s: patch ends past EOF (%#x)"
                             % (site.name, site.offset + site.size))
        derived = file_to_va(secs, site.offset)
        if derived != site.va:
            raise PatchError("%s: table says VA %#x but file offset %#x is VA %#x"
                             % (site.name, site.va, site.offset, derived))
        if site.name in CAVE_BODIES:
            sec = section_for_file_offset(secs, site.offset)
            if sec.name != ".rdata":
                raise PatchError("%s: cave body must live in .rdata" % site.name)
            if site.offset < cave_low or site.offset + site.size > cave_high:
                raise PatchError(
                    "%s: stub %#x..%#x does not fit the .rdata padding %#x..%#x"
                    % (site.name, site.offset, site.offset + site.size,
                       cave_low, cave_high))
    return True


# --- profile composition -----------------------------------------------------

def compose(profile, add=(), drop=()):
    """Return a Profile with sites added/removed, re-validating the cave groups."""
    base = PROFILES[profile] if isinstance(profile, str) else profile
    names = list(base.site_names)
    for name in add:
        if name not in SITE_BY_NAME:
            raise PatchError("unknown site %r (see --profile list)" % name)
        if name not in names:
            names.append(name)
    for name in drop:
        if name not in SITE_BY_NAME:
            raise PatchError("unknown site %r (see --profile list)" % name)
        if name in names:
            names.remove(name)
    for label, group in CAVE_GROUPS:
        present = [name for name in group if name in names]
        if present and len(present) != len(group):
            missing = [name for name in group if name not in names]
            raise PatchError("%s is incomplete: missing %s" % (label, ", ".join(missing)))
    maps = any(name in CAVE_BODIES for name in names)
    description = base.description
    if add or drop:
        description = "custom: %s" % "; ".join(
            part for part in (("add " + ", ".join(add)) if add else "",
                              ("drop " + ", ".join(drop)) if drop else "") if part)
    return Profile(base.name, description, names, maps)


# --- patching ----------------------------------------------------------------

def check_originals(data, profile):
    """Raise unless every site still holds its shipped bytes. Reports each site."""
    failures = []
    for site in profile.sites:
        end = site.offset + site.size
        if end > len(data):
            failures.append((site, None, "site ends past EOF (%#x > %#x)" % (end, len(data))))
            continue
        found = bytes(data[site.offset:end])
        if found != site.original:
            if found == site.patched:
                why = "already patched"
            else:
                why = "expected %s, found %s" % (site.original.hex(), found.hex())
            failures.append((site, found, why))
    if failures:
        lines = ["%d site(s) do not match this executable:" % len(failures)]
        for site, _found, why in failures:
            lines.append("  %-26s @%#010x (VA %#010x): %s"
                         % (site.name, site.offset, site.va, why))
        lines.append("refusing to write; patch a copy of the shipped client")
        raise PatchError("\n".join(lines))
    return True


def apply_patches(data, profile):
    """Apply a profile to a bytearray of the image. Returns a report list.

    Everything that can fail is checked before the first byte changes, so a
    refused patch leaves the caller's buffer exactly as it was.
    """
    check_originals(data, profile)
    rdata = _rdata(sections(data)) if profile.maps_rdata_exec else None
    if rdata is not None:
        needed = max(site.offset + site.size for site in profile.sites
                     if site.name in CAVE_BODIES) - rdata.rawptr
        if needed > RDATA_VIRTUALSIZE:
            raise PatchError("stubs need %#x bytes of .rdata but only %#x are mapped"
                             % (needed, RDATA_VIRTUALSIZE))
        if rdata.rawsize < RDATA_VIRTUALSIZE:
            raise PatchError(".rdata SizeOfRawData %#x cannot hold VirtualSize %#x"
                             % (rdata.rawsize, RDATA_VIRTUALSIZE))
    report = []
    for site in profile.sites:
        data[site.offset:site.offset + site.size] = site.patched
        report.append("  " + site.describe())
    if rdata is not None:
        if rdata.vsize < RDATA_VIRTUALSIZE:
            struct.pack_into("<I", data, rdata.header + 8, RDATA_VIRTUALSIZE)
            report.append("  .rdata VirtualSize %#x -> %#x"
                          % (rdata.vsize, RDATA_VIRTUALSIZE))
        chars = _u32(data, rdata.header + 36)
        if not chars & MEM_EXECUTE:
            struct.pack_into("<I", data, rdata.header + 36, chars | MEM_EXECUTE)
            report.append("  .rdata Characteristics %#x -> %#x" % (chars, chars | MEM_EXECUTE))
    checksum_at = checksum_offset(data)
    old_sum = _u32(data, checksum_at)
    new_sum = compute_checksum(data)
    struct.pack_into("<I", data, checksum_at, new_sum)
    report.append("  checksum %#010x -> %#010x" % (old_sum, new_sum))
    return report


def apply_file(src, dst, profile):
    """Patch src into dst. Nothing is written unless every site matched."""
    if os.path.exists(dst) and os.path.exists(src) and os.path.samefile(src, dst):
        raise PatchError("refusing to overwrite %s in place; patch a copy" % src)
    with open(src, "rb") as fh:
        data = bytearray(fh.read())
    source_sha1 = hashlib.sha1(data).hexdigest()
    validate_geometry(data, profile.sites)
    report = apply_patches(data, profile)
    tmp = dst + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, dst)
    return {
        "source": src,
        "source_sha1": source_sha1,
        "output": dst,
        "output_sha1": hashlib.sha1(data).hexdigest(),
        "checksum": _u32(data, checksum_offset(data)),
        "report": report,
        "profile": profile,
    }


def verify(data, profile):
    """Check an already patched image. Returns (ok, rows) with per-site detail."""
    rows = []
    ok = True
    for site in profile.sites:
        found = bytes(data[site.offset:site.offset + site.size])
        good = found == site.patched
        ok = ok and good
        detail = "ok" if good else "found %s, expected %s" % (found.hex(), site.patched.hex())
        rows.append((site, good, detail))
    if profile.maps_rdata_exec:
        rdata = _rdata(sections(data))
        good = rdata.vsize >= RDATA_VIRTUALSIZE
        ok = ok and good
        rows.append((".rdata VirtualSize", good, "%#x" % rdata.vsize))
        chars = _u32(data, rdata.header + 36)
        good = bool(chars & MEM_EXECUTE)
        ok = ok and good
        rows.append((".rdata Characteristics", good, "%#x" % chars))
    stored = _u32(data, checksum_offset(data))
    computed = compute_checksum(data)
    good = stored == computed
    ok = ok and good
    rows.append(("PE checksum", good, "stored %#010x, computed %#010x" % (stored, computed)))
    return ok, rows


def verify_file(path, profile):
    with open(path, "rb") as fh:
        data = bytearray(fh.read())
    validate_geometry(data, profile.sites)
    return verify(data, profile)


# --- CLI ---------------------------------------------------------------------

def _print_table():
    print("Streamline.exe patch sites (all offsets are for the shipped 65,996,288-byte build)")
    print("")
    for site in SITES:
        users = [name for name, prof in sorted(PROFILES.items()) if site.name in prof.site_names]
        print(site.describe() + "  [%s]" % (", ".join(users) if users else "override only"))
        print("    %s" % site.purpose)
    print("")
    for name in ("host", "host-min"):
        prof = PROFILES[name]
        print("%-9s %d sites: %s" % (name, len(prof.sites), ", ".join(prof.site_names)))
        print("          %s" % prof.description)
    print("")
    print("A profile with a cave body also maps the .rdata tail (VirtualSize %#x plus"
          % RDATA_VIRTUALSIZE)
    print("MEM_EXECUTE). Every patch recomputes the PE checksum.")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="patch_client.py",
        description="Patch your own Streamline.exe into the host builds.",
    )
    parser.add_argument("--profile", required=True,
                        help="host, host-min, or list")
    parser.add_argument("--in", dest="src", metavar="EXE",
                        help="your unpatched Streamline.exe")
    parser.add_argument("--out", dest="dst", metavar="EXE",
                        help="patched copy to write (never the input)")
    parser.add_argument("--verify", nargs="?", const="", metavar="EXE", default=None,
                        help="verify an existing patched copy instead of writing one")
    parser.add_argument("--add", action="append", default=[], metavar="SITE",
                        help="add one site to the profile (repeatable)")
    parser.add_argument("--drop", action="append", default=[], metavar="SITE",
                        help="drop one site from the profile (repeatable)")
    args = parser.parse_args(argv)

    if args.profile == "list":
        _print_table()
        return 0
    if args.profile not in PROFILES:
        parser.error("unknown profile %r (host, host-min, list)" % args.profile)

    try:
        profile = compose(args.profile, add=args.add, drop=args.drop)
    except PatchError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    if args.verify is not None:
        path = args.verify or args.src
        if not path:
            parser.error("--verify needs a path (or --in)")
        try:
            ok, rows = verify_file(path, profile)
        except (PatchError, OSError) as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 2
        print("verify %s with profile %s (%d sites)"
              % (path, profile.name, len(profile.sites)))
        for name, good, detail in rows:
            label = name if isinstance(name, str) else name.name
            print("  %-26s %-4s %s" % (label, "OK" if good else "FAIL", detail))
        print("VERDICT:", "PASS" if ok else "FAIL")
        return 0 if ok else 1

    if not args.src or not args.dst:
        parser.error("--in and --out are required (or use --verify / --profile list)")
    try:
        result = apply_file(args.src, args.dst, profile)
    except (PatchError, OSError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    print("profile %s: %d sites" % (profile.name, len(profile.sites)))
    for line in result["report"]:
        print(line)
    print("wrote %s" % result["output"])
    print("source sha1 %s" % result["source_sha1"])
    print("output sha1 %s" % result["output_sha1"])
    return 0


validate_table(SITES)


if __name__ == "__main__":
    sys.exit(main())
