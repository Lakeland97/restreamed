Restreamed
==========

Community server and tooling for Streamline (Proletariat, 2016), a 16-player arena game
whose official servers shut down in April 2018. This project implements the backend the
client expects and documents the client patches needed to run it locally.

The tooling runs on Python 3.9 or newer with only the standard library. The game is Windows-only; the
patchers edit files, so they run on any platform with Python.

Host and join
-------------

1. **Get your own copy of the game.** You need your own legally obtained copy; nothing in this
   repository ships game code or assets.
2. **Redirect the pak, once per copy.** The client's backend URL lives in the pak:
   `python3 tools/pak_patch_url.py "<copy>/hns/Content/Paks/hns-WindowsNoEditor.pak" \
   'https://api.playstreamline.com' 'http://127.0.0.1:8096/////////'`
   The address in the pak is where the client dials, so it must match `GUL_BASE` exactly. The patch
   is same-length: the trailing `/` characters pad the replacement to the original length, so a
   longer host name leaves less room and can run out.
3. **Patch copy A as the host:**
   `python3 tools/patch_client.py --profile host-play --in "<A>/hns/Binaries/Win64/Streamline.exe" --out Streamline.host.exe`
   then put `Streamline.host.exe` back as `Streamline.exe` and write a `listen.ini` next to it:

       [/Script/EngineSettings.GameMapsSettings]
       GameDefaultMap=/Game/Maps/GameCon/GameCon_PersistentLevel
       LocalMapOptions=?game=Tag?listen?IsHost=true?PlayerSessionID=host-1

   Use `host-play`, not `host`: the host also has to pass the launcher check and reach the mock
   login, and the bare `host` set stops at that check. Pass the map file on the command line with
   `-EngineIni=<path>\listen.ini` — a fresh copy may not have the config directory the engine reads,
   and the flag works regardless.

4. **Patch copy B as the joiner:** `--profile play`.
5. **Start the mock** where both copies can reach it:
   `python3 mock_appserver.py 8096`
   The defaults are the working configuration. For a LAN, set the pak address and `GUL_BASE` to the
   mock's address, for example `http://192.168.1.20:8096`, and set `GUL_GAME_HOST` to A's address:
   the join reply carries that host, so leaving it at `127.0.0.1` sends B to itself.
6. **Start A**, for example
   `Streamline.exe -NOSTEAM -EngineIni="<A>\listen.ini" -windowed -resx=1024 -resy=768`.
   It boots GameCon as a listen server on UDP 7777 and waits for a second player.
7. **Start B.** It logs in through the mock; the mock's `processUrl` reply carries the join link,
   and B travels into A's match. If B stays on HOME, launch it with
   `-RunURL=streamline:/join/game/1`.
8. **Expect:** B logs `Welcomed by server (Level: /Game/Maps/GameCon…)`, A logs
   `Join succeeded`, and both render the round intro, HUD and timer.

If a copy is launched directly rather than through Steam, add the `client` profile to it (or use
`--profile play`, which includes it). Steam supplies the launcher identity itself; a direct launch
needs the cave that answers the same request.

Status
------

### What works

- Login handshake and session context against the local server, through to the HOME menu.
- A client-hosted listen server on UDP 7777, and a second client joining it by native travel,
  with round phases replicated to both peers, rendering and playing the live match (the `play`
  profile removes the loading screen a native-travel join leaves up).
- Player identity and contestant registration in a match.
- The full round lifecycle on the host, with timed rounds, through MatchOver to the awards UI, then
  back to `WaitingForPlayers` and the next round.
- A finished round records its winner and the score it carries into the match payload.
- Joining from a server-sent join URL: the mock's `processUrl` reply reaches the handler and the client
  joins the returned listen host (`Welcomed`; the host logs `Join succeeded`).
- Practice, challenge and tutorial modes load and run locally. The tutorial is one-shot: after it,
  `FirstMatchData.PlayedTutorial` in `GameUserSettings.ini` stays true and the entry modal does not
  return until that value is set back to false.
- The community registry serves the server list the overlay reads.
- An out-of-process overlay (`tools/overlay`) for clients that are not patched: it arms the map-loaded
  latch so a native-travel join renders, and hosts the find/host panels as triggers into the game's
  own menus.
- The stream layer's read side: with `GUL_STREAM` the mock serves a deterministic mod rotation
  (four slots, one per bingo group, each mod timed by its own duration and drawn by its template
  weight), the channel mod schedule, and a profile built from the same inputs, alongside the poll
  endpoints the client asks for.
- The local backend and patcher: `mock_appserver.py` speaks the client's haxe.remoting dialect and
  returns the datastore payloads that finish login and seed a session; the patcher verifies every byte
  it replaces and repairs the PE checksum; an offline suite covers the serializer, entity payloads,
  mock replies and patcher tables without the game.

### What does not work

- **Steering which gameplay mod a match uses.** A hosted match applies the game's own catalogue mods
  on its own — a mod, its HUD card and its effect appear without the service — so making a mod happen
  is solved. Feeding the local rotation's choice into a live match is not: the client's mod state is
  scripted state, not the replicated `AppliedMod` arrays, and those arrays stay empty in hosted play.
  What the layer was, what the client still expects and what a local replacement has to provide:
  [StreamLayer](https://github.com/Lakeland97/restreamed/wiki/StreamLayer).
- A silent wardrobe selection for every item. With `all-cosmetics-equip` the slot pages open, a pick
  applies the item and it persists. An item the player owns applies with no dialog. An item outside the
  player's starting set applies but the post-apply refresh reports
  `Entity <item> does not exist in collection` in a handled dialog: the inventory holds the 48 starting
  items and the catalogue is larger. The client stays up and keeps the cosmetic.
- The host has no appserver session, so entity lookups miss. The effect is limited to the UI.

### What this changes, and what comes next

The backend is a local stand-in, not the original service. Nothing here talks to Twitch or to the
shut-down PGS: identities, sessions and datastore payloads are synthesized by the mock, and the launcher
identity request is answered locally instead of by Steam or the Twitch launcher.

Patches are applied to your own executable and every byte is verified before writing. They skip the
launcher check, keep a joined client off the loading widget, and make a round wait for a second player
(`wait_two`) — deliberate deviations, each listed on
[PatchIndex](https://github.com/Lakeland97/restreamed/wiki/PatchIndex).

Cosmetics are unlocked locally rather than bought: `all-cosmetics-equip` supplies the full id set and the
equip fixes. There is no commerce path in the service — flash sales, wagers and bets are not implemented,
so nothing is priced or charged.

Matches are hosted by a player's client on UDP 7777 and joined through the mock's `processUrl` reply,
not through the original matchmaking or a dedicated server.

Next:

- **Steer the mod a match applies.** The game already chooses and applies catalogue mods by itself; the
  served rotation is not fed in, and the client's own stream layer cannot be started from a client build —
  its entry operation lives on a private service the client never calls. Steering the game's own choice
  is the open piece.
- **A host that is not a player**, so a match does not depend on one person's client.
- **The remaining vote legs**: wagers, bets and flash sales.

Design notes, byte patches and open problems: [wiki](https://github.com/Lakeland97/restreamed/wiki).

Mock reference
--------------

```
python3 mock_appserver.py 8096
```

No environment is required: the working login configuration (datastore reply, entity template,
session context) is the default. The knobs below exist for A/B-testing an alternative reply.

| variable | default | selects |
|---|---|---|
| `GUL_BASE` | `http://127.0.0.1:8096` | the URL clients are told to use; set it to your LAN address |
| `GUL_BIND` | `0.0.0.0` | the interface the server listens on |
| `GUL_DSUR_B_OPS` | `startSession` | which remoting ops answer with the datastore update reply |
| `GUL_DSUR_HX` | `1` | `1` pushes the entity template alongside the entity; `0` sends the entity alone |
| `GUL_DSUR_HX_TEMPLATE` | `1` | include the template blob with the entity |
| `GUL_DSUR_HX_EMPTY_TEMPLATE` | `0` | send the template blob with no component definitions |
| `GUL_HS_MODE` | `resultdsur` | handshake reply shape; `error` returns a `Result.Error` marker |
| `GUL_USER_POLL_DELAY` | `10` | user-watcher long-poll hold, seconds |
| `GUL_USER_WATCH_HOLD` | the poll delay | explicit hold override for the user watcher |
| `GUL_USER_WATCH_STAMP` | `advance` | `echo` returns the requested stamp, so no update is delivered |
| `GUL_NAME_PREFIX` | `Player` | prefix for the auto-assigned display names (`Player1`, `Player2`, …) |
| `GUL_IID` | auto | pin `playerIID` for every session (single-client tests) |
| `GUL_NAME` | auto | pin the display name; pair it with `GUL_IID` |
| `GUL_COOKIES` | `0` | `1` issues the `hns_session` cookie so keyless legs keep the session identity (LAN tests with two clients) |
| `GUL_PGS_PARTY_ID` | `p1` | party id reported by the PGS party legs |
| `GUL_PGS_ROLES` | empty | comma-separated PGS roles on the user record; `broadcaster` makes NEW MATCH skip the stream sign-in and open the map picker |
| `GUL_OPEN_SERVER` | unset | `disabled` or `nofree` answer `getOpenServer` with those `OpenServerResponse` variants instead of `OSServer` |
| `GUL_GAME_HOST` | `127.0.0.1` | the host a joining client is told to travel to; set it to the host's LAN address |
| `GUL_SERVER_IP` | unset | answer `getServerIp` with `SIServer{host, port, party, playerSession, gameSession}`; unset answers `SIHostNotConnected`. Endpoint fields come from `GUL_GAME_HOST`, `GUL_GAME_PORT`, `GUL_JOIN_PARTY`, `GUL_JOIN_SESSION`, `GUL_JOIN_GAMESESSION` |
| `GUL_STREAM` | unset | `1` enables the stream legs: polls, votes, the rotation and `stream/profile` |
| `GUL_STREAM_MODE` | `tag` | allow-list the rotation draws from |
| `GUL_STREAM_WINDOW` | `25` | poll window, seconds |
| `GUL_STREAM_AUDIENCE` | `14` | size of the simulated audience |
| `GUL_STREAM_SEED` / `GUL_ROTATION_SEED` | `1` | seed the poll audience and the mod rotation |
| `GUL_STREAM_VIEWERS` | `42` | viewer count the live-channel entry reports |
| `GUL_STREAM_CHANNEL` | the session's identity id | channel id the stream legs answer for |
| `GUL_LOG` | `runtime/mock_appserver.log` | request log path (the log directory is created if missing) |

Each login gets its own display name (`Player1`, `Player2`, ...), so two clients show distinct names.
The module docstring of `mock_appserver.py` lists every knob, including the reply-shape experiments used
while reverse-engineering; the table above is the set a normal run needs.

Patching your client
--------------------

Patch a copy of your own executable; nothing patched is distributed here.

  python3 tools/patch_client.py --profile host-play \
      --in "/path/to/Streamline/hns/Binaries/Win64/Streamline.exe" \
      --out Streamline.host.exe

  python3 tools/patch_client.py --profile host-play --verify Streamline.host.exe
  python3 tools/patch_client.py --detect Streamline.host.exe
  python3 tools/patch_client.py --profile list

Profiles:

  client    answers the launcher identity request, so the stock login flow can reach the
            local server when the game is launched directly rather than through Steam.
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
  host-play the working host: host plus the launcher identity cave and the
            cosmetic/loading fixes, so it passes the launcher check, reaches the mock
            login and renders a joined match.
  host      the host byte patches and caves without the launcher identity or the
            cosmetic/loading fixes: identity, post-match loop, round timer, the
            login/admission byte patches and the match-data patches. Started directly
            it stops at the launcher check.
  host-min  the same byte patches without any cave: no .rdata mapping, no stubs, for
            bisecting which cave causes a problem.
  list      print every site with its virtual address, file offset and purpose.

`--add NAME` and `--drop NAME` override single sites of a profile.

The patcher holds only the original bytes it checks at each site and the stubs it injects, and it
refuses to write anything unless every original byte still matches: it reports the site that did
not, so a different game build fails loudly instead of producing a half-patched executable. It also
refuses to overwrite its own input.

The caves live in the zero padding at the end of `.rdata` and `.pdata`, so the patch raises each
used section's VirtualSize to its SizeOfRawData and sets MEM_EXECUTE. Every profile then recomputes
the PE optional-header checksum so the binary stays self-consistent. If you edit bytes yourself:

  python3 tools/fix_pe_checksum.py Streamline.host.exe

The pak patch is the other half: it redirects the client's backend URL.

  python3 tools/pak_patch_url.py "/path/to/Streamline/hns/Content/Paks/hns-WindowsNoEditor.pak" \
      'https://api.playstreamline.com' 'http://127.0.0.1:8096/////////'

Then start the game.

Layout
------

  mock_appserver.py          Reference implementation of the client wire protocol.
                             Speaks the game's haxe.remoting dialect, serves the PGS
                             legs (session, resource watchers, channels, polls), and
                             logs every exchange. Each watcher is answered under its
                             own plural key (`users`, `parties`, `channels`, ...).
                             This is the seed of the real server.

  tools/haxe_ser.py          Serializer/deserializer for the game's Haxe wire format,
                             including the datastore update response and entity streams.

  tools/hxinflate.py         Parser for entity type-info streams and inflated entities.

  tools/pak_patch_url.py     Same-length in-place pak patcher. Redirects the game's
                             backend URL in your own copy, with entry-hash refresh and
                             footer re-signing. Fully reversible.

  tools/pakdump.py           UE4 v3 pak reader: index listing and extraction.

  tools/registry_server.py   Community-server registry: serves the list the overlay
                             reads, accepts heartbeats, and TCP-probes entries so dead
                             servers drop out.

  tools/patch_client.py      Patch your own Streamline.exe into the working builds:
                             the login/admission byte patches, the identity cave, the
                             post-match loop and the round timer. Verifies every site
                             before writing and recomputes the checksum.

  tools/fix_pe_checksum.py   Recompute the PE optional-header checksum after editing
                             the executable yourself.

  tools/overlay/             Out-of-process overlay (ImGui) for clients that cannot be
                             patched: it arms the map-loaded latch so a native-travel
                             join renders, and hosts the find/host buttons. See its
                             README.

  tools/u16scan.py           UTF-16LE string extractor for binary triage.
  tools/unwind.py            Unwind/exception metadata helpers for the shipped binary.
  tools/stackscan.py         Stack-frame scanning helper.
  tools/throwscan.py         Exception/throw-site scanning helper.
  tools/memdump.py           Read-only process memory dump helper.
  tools/mdread.py            Minidump reader.

  tests/                     Offline contract tests for the serializer, entity payloads,
                             mock replies and patcher tables. No game launch required.

The original server domain
--------------------------

`api.playstreamline.com` is not operated by this project, and is not operated by
Proletariat, Valve, Blizzard Entertainment, Microsoft, or Twitch.

Do not point a client at it. Players who still own Streamline on Steam pass the launcher
check and reach that host directly, with no patch applied, and a server at that address
can send the client arbitrary data. Use the pak patch instead: it redirects the client to
a host you control and is fully reversible.

Roadmap
-------

Two projects follow: a replacement for the stream layer (in-game voting plus a simulated audience
driving the game's own mod system), and a host that is not a player. The server half of the stream layer
exists: polls, a simulated audience, and a vote-free rotation drawn from the game's own mod catalogue,
served behind `GUL_STREAM`, with the current profile readable at `GET stream/profile`. Applying a chosen
mod to a live match is the step still open. Both are described in
[StreamLayer](https://github.com/Lakeland97/restreamed/wiki/StreamLayer) and sequenced in the
[roadmap](https://github.com/Lakeland97/restreamed/wiki/Roadmap).

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
