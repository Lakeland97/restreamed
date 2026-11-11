# Overlay

Optional native overlay for the client: a community-server browser and a host panel,
drawn with ImGui over the game's own screen.

It is optional. A client built with the `play` profile (or `--add no_loading_screen`) leaves the
loading screen on its own. For an unpatched client the module arms the game's map-loaded flag eight
seconds after the widget appears (`ArmMapLoaded`), and the game's own tick then closes the screen.
Calling the close routine directly crashes the render thread.

The module hooks two menu handlers so they open a panel instead of running the game's
chain:

- Find Match -> community-server list (HTTP registry, `name|host:port` per line)
- New Match -> host panel

Selecting a row calls the game's own `ClientTravel`, so joining does not depend on the
server-browser of the game.

The host panel's button does not host inside this client: it writes a listen ini, starts a second
process (`Streamline.host.exe`, `-port=7777`) and joins it about 30 seconds later.

## Build

Needs a zig toolchain (0.13 or newer) and the ImGui 1.90.9 sources:

```
D3DCOMPILER_DIR=/path/to/importlib ZIG=/path/to/zig IMGUI=/path/to/imgui-1.90.9 ./build.sh
```

`libd3dcompiler47.a` is an import library for `d3dcompiler_47.dll`; generate it once with
`zig dlltool -m i386:x86-64 -d d3dcompiler_47.def -l libd3dcompiler47.a`, where the `.def` lists
`D3DCompile`, `D3DReflect` and `D3DCreateBlob`.

The build cross-compiles a Windows x64 DLL from macOS or Linux. No Windows toolchain
is required.

## Install

1. Copy `overlay.dll` next to `Streamline.exe` (in `hns/Binaries/Win64`).
2. The UCRT API-set DLLs (`api-ms-win-crt-*.dll`) must be resolvable from that
   directory; on a system without them the DLL fails to load with error 126.
3. Load the DLL into the running client (any injector) and call `ovl_show(1)`, or press
   Insert once loaded.

## Configuration

The DLL reads one ini file at startup and re-reads it every ten seconds. The path is the
`OVERLAY_INI` environment variable; when it is unset the file is `C:\Users\Public\overlay.ini`
(not the working directory). One `name=value` per line:

```
# community-server registry (the server panel)
host=192.168.64.1
port=8099
path=/servers.txt

# stream service (the mod poller); an empty stream_channel disables the poller
stream_host=192.168.64.1
stream_port=8096
stream_channel=
```

The registry is re-fetched every ten seconds; the stream poll runs once a second.

Registry format, one server per line:

```
Lab Server Alpha|127.0.0.1:7777
Remote Example|203.0.113.9:7777
```
