#!/usr/bin/env python3
"""Build a patched listen-host client.

All offsets are file offsets in the shipped Streamline.exe; the .text delta is
VA - 0xC00 and the .rdata delta is VA - 0x140000E00.

Usage: python3 tools/build_host.py OUT.exe [patch ...]
       (no patch names = the standard ni13 set)
"""
import hashlib
import shutil
import struct
import sys

import os
SRC = os.environ.get("REA_SRC", "game/hns/Binaries/Win64/Streamline.exe")

# Key names follow the lab build history, not the wiki ids. Mapping (verified by byte-read):
#   activate_server_ret    0xD47A50  = LoggedInPlayer.startLogin ret        (wiki H3)
#   set_player_defaults_skip 0xB7D8F0 = activateServer                      (wiki H6)
#   startlogin_skip        0x7B02E3  = SetPlayerDefaults kick branch        (wiki H5)
#   register_server_skip   0x7B21AF  = registerServer                       (wiki H10)
#   prelogin_nohost        0x7BA92D  = "No game session ID was provided"    (wiki H7)
#   prelogin_session       0x7BB1C9  = "No host is currently on the server" (wiki H8)
BASE = {
    "login_startplay_gate": (0x7B1E2E, "84c0", "31c0"),
    "login_retain": (0x16FFC22, "7f95", "9090"),
    "activate_server_ret": (0xD47A50, "40", "c3"),
    "startlogin_skip": (0x7B02E3, "84c0", "31c0"),
    "set_player_defaults_skip": (0xB7D8F0, "48895c2410", "c390909090"),
    "register_server_skip": (0x7B21AF, "84c0", "0c01"),
    "prelogin_nohost": (0x7BA92D, "84c0", "31c0"),
    "prelogin_session": (0x7BB1C9, "84c0", "31c0"),
    "forced_registered": (0x7B9B13, "0f94c3", "30db90"),
    "inactivity_kick_ret": (0xCC55C0, "40", "c3"),
    "p3_force_loaded": (0x7B6720, "40535556", "b001c390"),
    "gate5b_nop": (0x718840, "741b", "9090"),
    "matchover_s2": (0xCBF572, "ff9080020000", "90" * 6),
    "matchover_m2": (0xCBF5BE, "0f84d9050000", "90" * 6),
    "wait_two": (
        0x7B67C8,
        "85c00f8e10010000488b5c2478488b7c2478660f1f440000",
        "83f8020f8c25010000e90a010000" + "90" * 10,
    ),
    # contestants: rebuild at StartingRound instead of WaitingForPlayers
    "contestants_rebuild_starting": (0xB8029E, "26510403", "56510403"),
    # contestants diagnostic: key by display name on both sides
    "contestants_name_key_1": (0xB9726F, "e8cc01c5ff", "e82c27d0ff"),
    "contestants_name_key_2": (0xB97368, "e8d300c5ff", "e83326d0ff"),
    "contestants_name_producer_lo": (0xB8051A, "8b8388000000", "8b8310000000"),
    "contestants_name_producer_hi": (0xB80524, "488b8390000000", "488b8318000000"),
    # allow the restart when the appserver-loaded container is empty (MATCH_PAWN.md)
    "pawn_restart_allow": (0x7D4C33, "7514", "9090"),
    "send_match_data_bypass": (0xB94BEE, "75", "eb"),
    "send_match_data_post_skip": (0xB95C64, "e817c6d5ff", "9090909090"),
    "score_serialize_one": (0x7E9DAD, "e87e44aeff", "b801000000"),
    "round_end_le2": (0xCCE7F2, "01", "02"),
}

STD = [
    "login_startplay_gate",
    "login_retain",
    "activate_server_ret",
    "startlogin_skip",
    "set_player_defaults_skip",
    "register_server_skip",
    "prelogin_nohost",
    "prelogin_session",
    "forced_registered",
    "inactivity_kick_ret",
    "gate5b_nop",
    "matchover_s2",
    "matchover_m2",
    "wait_two",
]

CONTESTANTS = [
    "contestants_rebuild_starting",
    "contestants_name_key_1",
    "contestants_name_key_2",
    "contestants_name_producer_lo",
    "contestants_name_producer_hi",
]


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    out = argv[1]
    names = argv[2:] or STD + CONTESTANTS
    shutil.copyfile(SRC, out)
    d = bytearray(open(SRC, "rb").read())
    for name in names:
        off, old, new = BASE[name]
        old_b, new_b = bytes.fromhex(old), bytes.fromhex(new)
        assert len(old_b) == len(new_b), name
        cur = bytes(d[off:off + len(old_b)])
        assert cur == old_b, (name, hex(off), cur.hex(), old)
        d[off:off + len(old_b)] = new_b
        print(f"{name:34s} {off:#08x} {old} -> {new}")
    with open(out, "wb") as fh:
        fh.write(d)
    print("wrote", out, "patches:", len(names))
    print("sha1", hashlib.sha1(d).hexdigest())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
