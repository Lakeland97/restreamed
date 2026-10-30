Restreamed
==========

Community server and tooling for Streamline (Proletariat, 2016), a 16-player arena game
whose official servers shut down in April 2018. This project implements the backend the
client expects and documents the client patches needed to run it locally.

Status
------

Works:

- Login handshake and session context against the local server, through to the HOME menu.
- A client-hosted listen server on UDP 7777 that waits for a second player.
- A second client joining by native travel, with round phases replicated to both peers.
- Player identity and contestant registration in a match.
- The full round lifecycle on the host, through MatchOver to the awards UI, then back to
  `WaitingForPlayers` and the next round.
- A finished round records its winner and the score it carries into the match payload.
- Practice, challenge and tutorial modes load and run locally. The tutorial is one-shot until
  its played flag is reset.

Does not work:

- The joined client is too slow to play.
- Joining from a server-sent join URL; the client crashes.
- The host has no appserver session, so entity lookups miss. The visible effect is cosmetic.

Design notes, byte patches and open problems: [wiki](https://github.com/Lakeland97/restreamed/wiki).

Layout
------

  mock_appserver.py          Reference implementation of the client wire protocol.
                             Speaks the game's haxe.remoting dialect and logs every
                             exchange. This is the seed of the real server.

  tools/haxe_ser.py          Serializer/deserializer for the game's Haxe wire format,
                             including the datastore update response and entity streams.

  tools/hxinflate.py         Parser for entity type-info streams and inflated entities.

  tools/pak_patch_url.py     Same-length in-place pak patcher. Redirects the game's
                             backend URL in your own copy, with entry-hash refresh and
                             footer re-signing. Fully reversible.

  tools/pakdump.py           UE4 v3 pak reader: index listing and extraction.

  tools/fix_pe_checksum.py   Recompute the PE optional-header checksum after patching the
                             client executable. The client's DRM path refuses a modified
                             executable until the checksum matches the file.

  tools/u16scan.py           UTF-16LE string extractor for binary triage.
  tools/unwind.py            Unwind/exception metadata helpers for the shipped binary.
  tools/stackscan.py         Stack-frame scanning helper.
  tools/throwscan.py         Exception/throw-site scanning helper.
  tools/memdump.py           Read-only process memory dump helper.
  tools/mdread.py            Minidump reader.

  tests/                     Offline contract tests for the serializer, entity payloads,
                             and mock replies. No game launch required.

Running the mock
----------------

  python3 mock_appserver.py 8096

The server listens on the given port and answers the client's remoting calls. Point your
patched client at the host running it.

Building the client patch
-------------------------

  python3 tools/pak_patch_url.py "/path/to/Streamline/hns/Content/Paks/hns-WindowsNoEditor.pak" \
      'https://api.playstreamline.com' 'http://127.0.0.1:8096/////////'

Then start the game.

If you patch the executable itself rather than the pak, recompute its PE checksum:

  python3 tools/fix_pe_checksum.py "/path/to/Streamline/hns/Binaries/Win64/Streamline.exe"

Without that, the client reports that it must be run through Steam or the Twitch launcher.

The original server domain
--------------------------

`api.playstreamline.com` is not operated by this project, and is not operated by
Proletariat, Valve, Blizzard Entertainment, Microsoft, or Twitch.

Do not point a client at it. Players who still own Streamline on Steam pass the launcher
check and reach that host directly, with no patch applied, and a server at that address
can send the client arbitrary data. Use the pak patch instead: it redirects the client to
a host you control and is fully reversible.

Contributing
------------

See [CONTRIBUTING.md](CONTRIBUTING.md).

Legal
-----

This repository contains only original tools and code: no game files, no decompiled
code, and no assets. The pak patcher applies a byte-level patch to your own copy of the
game; nothing is redistributed. The server is written from protocol analysis and observed
client behavior. You need your own legally obtained copy of the game to use any of this.

License
-------

MIT. See LICENSE.
