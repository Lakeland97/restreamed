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
- A second client joining by native travel, with round phases replicated to both peers, rendering and
  playing the live match (the `play` profile removes the loading screen a native-travel join leaves up).
- Player identity and contestant registration in a match.
- The full round lifecycle on the host, with timed rounds, through MatchOver to the awards UI, then
  back to `WaitingForPlayers` and the next round.
- A finished round records its winner and the score it carries into the match payload.
- Joining from a server-sent join URL: the mock's `processUrl` reply reaches the handler and the client
  joins the returned listen host (`Welcomed`; the host logs `Join succeeded`).
- Practice, challenge and tutorial modes load and run locally. The tutorial is one-shot until
  its played flag is reset.

Does not work:

- The stream layer. Streamline's original design paired the client with a web front end and a live
  service that ran chat polls, rule votes, bets, flash sales and gameplay mods. None of that is
  restored: there is no Twitch login, no stream page, and no poll feed, so the mod system the client
  still contains never starts. See "Where this is going" below.
- A silent wardrobe selection for every item. With `all-cosmetics-equip` the slot pages open, a pick
  applies the item and it persists (it follows to HOME and other pages). An item the player owns
  applies with no dialog. An item outside the player's starting set applies but the post-apply refresh
  reports `Entity <item> does not exist in collection` in a handled dialog — the inventory holds the
  48 starting items and the catalogue is larger than that. The client stays up; the cosmetic is kept.
- The host has no appserver session, so entity lookups miss. The visible effect is cosmetic.

Design notes, byte patches and open problems: [wiki](https://github.com/Lakeland97/restreamed/wiki).

What this project does, what it changes, and where it is going
--------------------------------------------------------------

### Implemented

- **A working local backend.** `mock_appserver.py` speaks the client's haxe.remoting dialect and
  returns the datastore payloads the client needs to finish login, seed a session, and name each
  player (`Player1`, `Player2`, ...).
- **A patcher for your own client.** Byte-verified sites for the launcher identity, the HOME player
  model, the join loading screen, the cosmetic catalogue and selection, the host admission patches,
  the post-match loop and the round timer, with a PE checksum repair and a same-length pak URL
  redirect.
- **Playable matches.** A client-hosted listen server, a second client joining by native travel,
  replicated round phases, the full round lifecycle to MatchOver and back to `WaitingForPlayers`, and
  the server-sent join link that routes a client into a host.
- **Cosmetics.** Every catalogue item can be browsed; owned items equip with no dialog, unowned ones
  equip and report the missing container entry.
- **Offline tests.** The serializer, entity payloads, mock replies and patcher tables are covered by
  a test suite that runs without the game.

### Liberties we took

- **The host is a player.** The shipped game has no dedicated-server build, so "server" here means a
  patched client hosting a listen match.
- **The backend is reconstructed, not recovered.** Request and reply shapes are derived from how the
  client parses them, not from any original server source. Where the client tolerated a plausible
  payload we used it.
- **Identity is faked at the launcher boundary.** A small code cave returns a fixed DRM identity
  instead of passing Steam or Twitch authentication.
- **The join loading screen is bypassed, not satisfied.** A native-travel join never arms the client's
  map-loaded latch, so the `play` profile returns early from `openLocalLoadingScreen`; the overlay does
  the same from outside the process.
- **The cosmetic catalogue is widened.** The client is patched to list the whole catalogue instead of
  the owned set, and a template singleton takes its template id as its instance id so an owned pick
  resolves. Items outside the 48 starting items still have no container entry; they equip and persist,
  but the client reports the missing entry.
- **Onboarding is a setting, not a feature.** Marking the tutorial played is a launch-time settings
  value; nothing on the server models it.

### Where this is going: an orchestrated stream layer

Streamline was built as two halves: the arena client, and a live layer that watched a Twitch channel
and turned audience actions into gameplay. The client still carries that half — `sl.game.pgs.PGSManager`,
`ModVotingManager`, `BettingManager`, `FlashSaleManager`, `BingoManager`, `WagerGame`, the mod
components (`ModComponent`, `ModGroupComponent`, `PropertyModManager`), `StreamoteUtils`, and the
endpoint paths `/poll`, `/vote/`, `/chat/say`, `/broadcast` alongside Twitch IRC and the Twitch API.
None of it is connected today, which is why the "Stream" in Streamline is missing.

The goal is not to bring Twitch back. It is to drive the same machinery locally and deliberately:

- **In-game voting.** Players vote on the next rule, physics change or mini-game between rounds,
  using the client's own voting and mod pipeline.
- **A simulated audience.** An algorithm stands in for the stream: it proposes mods, votes with a
  believable spread, and keeps the cadence of a live channel so a solo player or a small group still
  gets the original rhythm.
- **Real consequences.** Whatever wins is applied through the existing mod system, so physics, rules
  and mini-games actually change.

That work is research plus a new server-side feature; it is not implemented yet.

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

  tools/registry_server.py   Minimal community-server registry: serves the list
                             the overlay reads, accepts heartbeats, and can
                             TCP-probe entries so dead servers drop out.

  tools/fix_pe_checksum.py   Recompute the PE optional-header checksum after patching the
                             client executable so the binary stays self-consistent. Not a
                             DRM gate: the identity check is launcher-based, and the shipped
                             binary's own stored checksum is already stale.

  tools/patch_client.py      Patch your own Streamline.exe into the host builds: the
                             login/admission byte patches, the identity cave, the
                             post-match loop and the round timer. Verifies every site
                             before writing and recomputes the checksum.

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

The login flow was verified with the branch-B datastore reply enabled. Without these
variables the `startSession` reply carries no entity or template (726 bytes instead of
1053) and the client cannot seed its datastore:

  export GUL_BIND=0.0.0.0
  export GUL_BASE=http://127.0.0.1:8096
  export GUL_DSUR_B_OPS=startSession
  export GUL_DSUR_HX=1
  export GUL_DSUR_HX_PURPOSE=game
  export GUL_DSUR_HX_TEMPLATE=1
  export GUL_DSUR_HX_TAIL=0
  export GUL_HS_MODE=resultdsur
  export GUL_CTX_PROVIDER_ENUM=1
  export GUL_CTX_PROVIDER_IDX=2
  export GUL_USER_POLL_DELAY=10
  export GUL_TRACK_DELAY=0
  export GUL_M_DRM=3
  export GUL_IID_TEMPLATE=player
  export GUL_IID_TEMPLATE_BLOB=player
  export GUL_URL_ACTION=join
  export GUL_TRACK_STYLE=result
  export GUL_NOCL=0
  python3 mock_appserver.py 8096

On Windows, set the same names with `set GUL_...=...` (or in the system environment) before running
the command. The server listens on the given port and answers the client's remoting calls. Set
`GUL_BASE` to the address the client uses for the server; `127.0.0.1` when both run on
the same machine. Each login gets its own display name (`Player1`, `Player2`, ...), so two
clients show distinct names. Point your patched client at this host.

Patching your client
--------------------

Patch a copy of your own executable; nothing patched is distributed here.

  python3 tools/patch_client.py --profile host \
      --in "/path/to/Streamline/hns/Binaries/Win64/Streamline.exe" \
      --out Streamline.host.exe

  python3 tools/patch_client.py --profile host --verify Streamline.host.exe
  python3 tools/patch_client.py --profile list

Profiles:

  client    passes the launcher check (DRM identity cave), so the stock login
            flow can reach the local server.
  home-model  client plus the cosmetics fix: the HOME player model is dressed.
  play      home-model plus the loading-screen fix: a native-travel join reaches the
            match instead of staying on the LOADING widget.
  all-cosmetics  play plus the full cosmetic id set and the null-id guard: every
            slot page opens and lists its items.
  all-cosmetics-equip  the above plus the selection caves and the item-id fix: an
            out-of-range or unowned pick reports the game's error instead of
            crashing, a listed item applies the entity the UI holds, and an item the
            player owns applies silently. Items the player does not own still report
            the missing container entry (see the limitation above).
  host      the full working host set: identity cave, post-match loop cave, round
            timer cave, the login/admission byte patches and the match-data patches.
  host-min  the same byte patches without any cave: no .rdata mapping, no stubs.
  list      print every site with its virtual address, file offset and purpose.

A two-client test needs the pak URL patch on both copies, `play` on the joining
copy and `host` on the hosting copy.

The joining client needs the loading-screen fix (`play`, or `--add no_loading_screen` to another
profile). A native-travel join never sets the client's map-loaded flag, so the game never dismisses the
widget it creates; the fix returns from `openLocalLoadingScreen` instead, and the client renders the
match directly. An unpatched client can use the [overlay](tools/overlay/README.md) instead: it arms the
latch (`ArmMapLoaded`) eight seconds after the widget appears and the client's own tick closes the
screen. Calling the close routine directly crashes the render thread.

The `host` build also needs the listen map configuration, or it boots as a client:

  [/Script/EngineSettings.GameMapsSettings]
  GameDefaultMap=/Game/Maps/GameCon/GameCon_PersistentLevel
  LocalMapOptions=?game=Tag?listen?IsHost=true?PlayerSessionID=host-1

`--add NAME` and `--drop NAME` override single sites of a profile.

The patcher holds only the original bytes it checks at each site and the stubs it
injects, and it refuses to write anything unless every original byte still matches:
it reports the site that did not, so a different game build fails loudly instead of
producing a half-patched executable. It also refuses to overwrite its own input.

The caves live in the zero padding at the end of `.rdata` and `.pdata`, so the patch
raises each used section's VirtualSize to its SizeOfRawData and sets MEM_EXECUTE. Every
profile then recomputes the PE optional-header checksum so the binary stays self-consistent
(hygiene, not a DRM gate: the identity check is launcher-based, and the shipped binary's
own stored checksum is already stale):

  python3 tools/fix_pe_checksum.py Streamline.host.exe        # if you edited bytes yourself

The pak patch below is the other half: it redirects the client's backend URL.

  python3 tools/pak_patch_url.py "/path/to/Streamline/hns/Content/Paks/hns-WindowsNoEditor.pak" \
      'https://api.playstreamline.com' 'http://127.0.0.1:8096/////////'

Then start the game.

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
