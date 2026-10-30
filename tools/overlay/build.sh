#!/usr/bin/env bash
# Build the overlay DLL with zig (clang + mingw-w64, no Windows toolchain needed).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ZIG="${ZIG:?set ZIG to the zig binary}"
IMGUI="${IMGUI:?set IMGUI to the imgui source directory}"
OUT="${OUT:-$HERE/overlay.dll}"

"$ZIG" cc -target x86_64-windows-gnu -shared -O2 -std=c++17 -fno-exceptions -fno-rtti -fno-builtin \
  -I"$IMGUI" -I"$IMGUI/backends" \
  "$HERE/overlay.cpp" \
  "$IMGUI/imgui.cpp" "$IMGUI/imgui_draw.cpp" "$IMGUI/imgui_tables.cpp" "$IMGUI/imgui_widgets.cpp" \
  "$IMGUI/backends/imgui_impl_win32.cpp" "$IMGUI/backends/imgui_impl_dx11.cpp" \
  -ld3d11 -ldxgi -lwinhttp -lshcore -ldwmapi -ldxguid -luser32 -lgdi32 -limm32 -lole32 -luuid \
  -o "$OUT"

ls -lh "$OUT"
