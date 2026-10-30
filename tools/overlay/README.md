# Overlay

Optional native overlay for the client: a community-server browser and a host panel,
drawn with ImGui over the game's own screen.

It is optional for hosting and for HOME, and required for a joining client: a native-travel join
never sets the client's map-loaded flag, so the game's loading screen is never dismissed. The module
arms that flag eight seconds after the widget appears (`ArmMapLoaded`) and the game's own tick then
closes the screen. Calling the close routine directly crashes the render thread.

The module hooks two menu handlers so they open a panel instead of running the game's
chain:

- Find Match -> community-server list (HTTP registry, `name|host:port` per line)
- New Match -> host panel

Selecting a row calls the game's own `ClientTravel`, so joining does not depend on the
server-browser of the game.

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

`overlay.ini` (next to the working directory) sets the registry:

```
host=192.168.64.1
port=8099
path=/servers.txt
```

Registry format, one server per line:

```
Lab Server Alpha|127.0.0.1:7777
Remote Example|203.0.113.9:7777
```
