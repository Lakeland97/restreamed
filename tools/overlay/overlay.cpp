// Minimal ImGui overlay for Streamline (UE 4.11, D3D11).
// Injects a Present hook and draws an empty panel on Insert until the real
// panels are wired. Build with tools/overlay/build.sh (zig cc, mingw target).

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <winhttp.h>
#include <stdio.h>
#include <d3d11.h>
#include <dxgi.h>

#include "imgui.h"
#include "backends/imgui_impl_win32.h"
#include "backends/imgui_impl_dx11.h"

extern IMGUI_IMPL_API LRESULT ImGui_ImplWin32_WndProcHandler(HWND hWnd, UINT msg, WPARAM wParam, LPARAM lParam);

namespace {
struct ServerEntry { char name[64]; char addr[96]; int players; int max; };
ServerEntry g_servers[32];
int g_server_count = 0;
int g_click_count = 0;
int g_panel = 0;   // 0 = servers, 1 = find, 2 = host
void* g_menu_self = nullptr;
DWORD g_join_at = 0;
char g_game_dir[MAX_PATH] = {};
DWORD g_last_fetch = 0;

bool FetchRegistry(const char* host, int port, const char* path) {
    HINTERNET ses = WinHttpOpen(L"streamline-overlay/1", WINHTTP_ACCESS_TYPE_DEFAULT_PROXY,
                                WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
    if (!ses) return false;
    wchar_t whost[128] = {};
    MultiByteToWideChar(CP_UTF8, 0, host, -1, whost, 128);
    wchar_t wpath[256] = {};
    MultiByteToWideChar(CP_UTF8, 0, path, -1, wpath, 256);
    HINTERNET con = WinHttpConnect(ses, whost, (INTERNET_PORT)port, 0);
    if (!con) { WinHttpCloseHandle(ses); return false; }
    HINTERNET req = WinHttpOpenRequest(con, L"GET", wpath, nullptr, WINHTTP_NO_REFERER,
                                       WINHTTP_DEFAULT_ACCEPT_TYPES, 0);
    bool ok = false;
    if (req && WinHttpSendRequest(req, WINHTTP_NO_ADDITIONAL_HEADERS, 0, nullptr, 0, 0, 0) &&
        WinHttpReceiveResponse(req, nullptr)) {
        char buf[8192] = {};
        DWORD got = 0, total = 0;
        while (WinHttpReadData(req, buf + total, sizeof(buf) - 1 - total, &got) && got > 0 &&
               total + got < sizeof(buf) - 1) {
            total += got;
        }
        buf[total] = 0;
        int n = 0;
        char* line = buf;
        while (line && *line && n < 32) {
            char* nl = line;
            while (*nl && *nl != '\n' && *nl != '\r') ++nl;
            char saved = *nl;
            *nl = 0;
            char* bar = line;
            while (*bar && *bar != '|') ++bar;
            if (*bar == '|' && bar != line) {
                *bar = 0;
                strncpy(g_servers[n].name, line, sizeof(g_servers[n].name) - 1);
                char* addr = bar + 1;
                char* p2 = addr;
                while (*p2 && *p2 != '|') ++p2;
                if (*p2 == '|') {
                    *p2 = 0;
                    char* pl = p2 + 1;
                    char* p3 = pl;
                    while (*p3 && *p3 != '|') ++p3;
                    if (*p3 == '|') { *p3 = 0; g_servers[n].max = atoi(p3 + 1); }
                    g_servers[n].players = atoi(pl);
                }
                strncpy(g_servers[n].addr, addr, sizeof(g_servers[n].addr) - 1);
                ++n;
            }
            if (!saved) break;
            line = nl + 1;
            while (*line == '\r' || *line == '\n') ++line;
        }
        g_server_count = n;
        ok = true;
    }
    if (req) WinHttpCloseHandle(req);
    WinHttpCloseHandle(con);
    WinHttpCloseHandle(ses);
    return ok;
}

void FetchFromConfig() {
    char host[128] = "192.168.64.1";
    int port = 8099;
    char path[128] = "/servers.txt";
    char ini[MAX_PATH] = {};
    GetEnvironmentVariableA("OVERLAY_INI", ini, MAX_PATH);
    if (!ini[0]) strcpy(ini, "C:\\Users\\Public\\overlay.ini");
    FILE* f = fopen(ini, "r");
    if (f) {
        char line[256];
        while (fgets(line, sizeof(line), f)) {
            char* eq = strchr(line, '=');
            if (!eq) continue;
            *eq = 0;
            char* val = eq + 1;
            while (*val == ' ') ++val;
            char* end = val + strlen(val);
            while (end > val && (end[-1] == '\n' || end[-1] == '\r' || end[-1] == ' ')) *--end = 0;
            if (!strcmp(line, "host")) strncpy(host, val, sizeof(host) - 1);
            else if (!strcmp(line, "port")) port = atoi(val);
            else if (!strcmp(line, "path")) strncpy(path, val, sizeof(path) - 1);
        }
        fclose(f);
    }
    FetchRegistry(host, port, path);
    g_last_fetch = GetTickCount();
}


using PresentFn = HRESULT(WINAPI*)(IDXGISwapChain*, UINT, UINT);

PresentFn g_present = nullptr;
bool g_imgui_ready = false;
bool g_visible = false;
HWND g_hwnd = nullptr;
WNDPROC g_old_wndproc = nullptr;
ID3D11Device* g_device = nullptr;
ID3D11DeviceContext* g_context = nullptr;

LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wparam, LPARAM lparam) {
    if (g_visible && ImGui_ImplWin32_WndProcHandler(hwnd, msg, wparam, lparam)) {
        return 1;
    }
    if (msg == WM_KEYDOWN && wparam == VK_INSERT) {
        g_visible = !g_visible;
    }
    return CallWindowProcW(g_old_wndproc, hwnd, msg, wparam, lparam);
}

void InstallMenuHooks();

void InitImGui(IDXGISwapChain* swapchain) {
    if (g_imgui_ready) return;
    if (FAILED(swapchain->GetDevice(__uuidof(ID3D11Device), reinterpret_cast<void**>(&g_device)))) return;
    g_device->GetImmediateContext(&g_context);

    DXGI_SWAP_CHAIN_DESC desc{};
    if (FAILED(swapchain->GetDesc(&desc))) return;
    g_hwnd = desc.OutputWindow;
    g_old_wndproc = reinterpret_cast<WNDPROC>(
        SetWindowLongPtrW(g_hwnd, GWLP_WNDPROC, reinterpret_cast<LONG_PTR>(WndProc)));

    IMGUI_CHECKVERSION();
    ImGui::CreateContext();
    ImGuiIO& io = ImGui::GetIO();
    io.IniFilename = nullptr;
    ImGui::StyleColorsDark();
    ImGui_ImplWin32_Init(g_hwnd);
    ImGui_ImplDX11_Init(g_device, g_context);
    g_imgui_ready = true;
    InstallMenuHooks();
}


namespace {
using GetPCFn = void* (*)(void*, int);
using TravelFn = void (*)(void*, void*, int, bool, void*);

bool LocalTravel(const wchar_t* url) {
    HMODULE base = GetModuleHandleW(nullptr);
    auto at = [base](uintptr_t off) { return reinterpret_cast<uintptr_t>(base) + off; };
    void* g = *reinterpret_cast<void**>(at(0x3C2BAA0));
    if (!g) return false;
    void* arr = *reinterpret_cast<void**>(reinterpret_cast<uintptr_t>(g) + 0xB30);
    if (!arr) return false;
    void* ctx = *reinterpret_cast<void**>(arr);
    if (!ctx) return false;
    void* nw = *reinterpret_cast<void**>(reinterpret_cast<uintptr_t>(ctx) + 0x230);
    if (!nw) return false;
    auto get_pc = reinterpret_cast<GetPCFn>(at(0x16FA590));
    void* pc = get_pc(nw, 0);
    if (!pc) return false;

    size_t len = wcslen(url);
    struct { wchar_t* data; int num; int max; } fstr{const_cast<wchar_t*>(url), static_cast<int>(len + 1),
                                                     static_cast<int>(len + 1)};
    unsigned char guid[16] = {};
    auto travel = reinterpret_cast<TravelFn>(at(0x148FA60));
    travel(pc, &fstr, 0, false, guid);
    return true;
}
}  // namespace

extern "C" uintptr_t HookFind(void*) {
    g_panel = 1;
    g_visible = true;
    return 0;
}

extern "C" uintptr_t HookHost(void* self) {
    g_menu_self = self;
    g_panel = 2;
    g_visible = true;
    return 0;
}

void PatchJump(void* addr, void* dest) {
    DWORD old = 0;
    if (!VirtualProtect(addr, 12, PAGE_EXECUTE_READWRITE, &old)) return;
    unsigned char code[12] = {0x48, 0xB8};
    memcpy(code + 2, &dest, 8);
    code[10] = 0xFF;
    code[11] = 0xE0;
    memcpy(addr, code, 12);
    VirtualProtect(addr, 12, old, &old);
    FlushInstructionCache(GetCurrentProcess(), addr, 12);
}

void InstallMenuHooks() {
    HMODULE base = GetModuleHandleW(nullptr);
    auto at = [base](uintptr_t off) { return reinterpret_cast<void*>(reinterpret_cast<uintptr_t>(base) + off); };
    PatchJump(at(0x62EF80), reinterpret_cast<void*>(&HookFind));   // HomePlayMenu.onQuickmatch
    PatchJump(at(0x630E50), reinterpret_cast<void*>(&HookHost));   // HomePlayMenu.onHost
}

bool WriteListenIni(char* out_path, size_t out_size) {
    char tmp[MAX_PATH] = {};
    GetTempPathA(MAX_PATH, tmp);
    _snprintf(out_path, out_size, "%sstreamline_listen.ini", tmp);
    FILE* f = fopen(out_path, "w");
    if (!f) return false;
    fputs("[/Script/EngineSettings.GameMapsSettings]\n"
          "GameDefaultMap=/Game/Maps/GameCon/GameCon_PersistentLevel\n"
          "LocalMapOptions=?game=Tag?listen?IsHost=true?PlayerSessionID=host-1\n", f);
    fclose(f);
    return true;
}

bool LaunchHost() {
    char ini[MAX_PATH] = {};
    if (!WriteListenIni(ini, sizeof(ini))) return false;
    char exe[MAX_PATH] = {};
    _snprintf(exe, sizeof(exe), "%sStreamline.host.exe", g_game_dir);
    if (GetFileAttributesA(exe) == INVALID_FILE_ATTRIBUTES) return false;
    char log[MAX_PATH] = {};
    _snprintf(log, sizeof(log), "%sstreamline_host.log", g_game_dir);
    char cmd[1024] = {};
    _snprintf(cmd, sizeof(cmd),
              "\"%s\" -NOSTEAM -NOFUEL -nullrhi -nosound -unattended -nosplash -port=7777 "
              "-FORCELOGFLUSH -abslog=\"%s\" -EngineIni=\"%s\"",
              exe, log, ini);
    STARTUPINFOA si = {sizeof(si)};
    PROCESS_INFORMATION pi = {};
    if (!CreateProcessA(nullptr, cmd, nullptr, nullptr, FALSE, 0, nullptr, g_game_dir, &si, &pi)) return false;
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    g_join_at = GetTickCount() + 30000;   // host boot takes ~25-30 s here
    return true;
}

void Render() {
    if (!g_visible) return;
    if (GetTickCount() - g_last_fetch > 10000) FetchFromConfig();
    if (g_join_at && GetTickCount() >= g_join_at) {
        g_join_at = 0;
        LocalTravel(L"127.0.0.1:7777?Version=HNS-release.2.7.31");
    }
    ImGui_ImplDX11_NewFrame();
    ImGui_ImplWin32_NewFrame();
    ImGui::NewFrame();
    ImGui::SetNextWindowPos(ImVec2(40, 120), ImGuiCond_Always);
    ImGui::SetNextWindowSize(ImVec2(460, 300), ImGuiCond_Always);
    const char* title = g_panel == 2 ? "Host match" : "Community servers";
    ImGui::Begin(title, nullptr, ImGuiWindowFlags_NoResize | ImGuiWindowFlags_NoMove);
    if (g_panel == 2) {
        ImGui::TextUnformatted("Host settings (placeholder)");
        ImGui::Separator();
        ImGui::TextUnformatted("Start listen host on port 7777");
        if (ImGui::Button("Start host and join", ImVec2(-1, 0))) {
            ++g_click_count;
            LaunchHost();
        }
        if (g_join_at) ImGui::TextUnformatted("host starting, joining when it is up...");
    } else {
        ImGui::TextUnformatted("Community servers");
        ImGui::SameLine();
        if (ImGui::Button("Refresh", ImVec2(80, 0))) FetchFromConfig();
        ImGui::Separator();
        if (g_server_count == 0) {
            if (ImGui::Button("Local host  127.0.0.1:7777", ImVec2(-1, 0))) {
                ++g_click_count;
                LocalTravel(L"127.0.0.1:7777?Version=HNS-release.2.7.31");
            }
        }
        for (int i = 0; i < g_server_count; ++i) {
            char label[220];
            if (g_servers[i].max > 0) {
                sprintf(label, "%s   %d/%d   %s", g_servers[i].name, g_servers[i].players,
                        g_servers[i].max, g_servers[i].addr);
            } else {
                sprintf(label, "%s   %s", g_servers[i].name, g_servers[i].addr);
            }
            if (ImGui::Button(label, ImVec2(-1, 0))) {
                ++g_click_count;
                wchar_t waddr[256] = {};
                MultiByteToWideChar(CP_UTF8, 0, g_servers[i].addr, -1, waddr, 256);
                wcscat(waddr, L"?Version=HNS-release.2.7.31");
                LocalTravel(waddr);
            }
        }
    }
    ImGui::End();
    ImGui::Render();
    ImGui_ImplDX11_RenderDrawData(ImGui::GetDrawData());
}

// A native-travel join never tells the client the map loaded, so its loading
// screen stays. Arm the game's own latch and let its tick close the screen
// (calling the close routine directly crashes the render thread).
void ArmMapLoaded() {
    static DWORD since = 0;
    HMODULE base = GetModuleHandleW(nullptr);
    auto at = [base](uintptr_t off) { return reinterpret_cast<uintptr_t>(base) + off; };
    void** slot = reinterpret_cast<void**>(at(0x3BC5058));
    if (!slot || !*slot) { since = 0; return; }
    DWORD now = GetTickCount();
    if (!since) { since = now; return; }
    if (now - since < 8000) return;
    auto set_map = reinterpret_cast<void (*)(void)>(at(0x7DA770));
    set_map();
    since = 0;
}

HRESULT WINAPI HookPresent(IDXGISwapChain* self, UINT sync, UINT flags) {
    if (!g_present) return E_FAIL;
    if (!g_imgui_ready) InitImGui(self);
    ArmMapLoaded();
    if (g_imgui_ready && g_visible && g_device && g_context) {
        // The game unbinds its render target before Present, so bind the back
        // buffer ourselves or ImGui has nothing to draw into.
        ID3D11Texture2D* back = nullptr;
        if (SUCCEEDED(self->GetBuffer(0, __uuidof(ID3D11Texture2D), reinterpret_cast<void**>(&back))) && back) {
            ID3D11RenderTargetView* rtv = nullptr;
            if (SUCCEEDED(g_device->CreateRenderTargetView(back, nullptr, &rtv)) && rtv) {
                g_context->OMSetRenderTargets(1, &rtv, nullptr);
                Render();
                rtv->Release();
            }
            back->Release();
        }
    }
    return g_present(self, sync, flags);
}

// Patch the shared Present vtable entry once, using a throwaway swap chain to
// learn the address without touching the game's device.
bool HookSwapChain() {
    WNDCLASSEXW wc{};
    wc.cbSize = sizeof(wc);
    wc.lpfnWndProc = DefWindowProcW;
    wc.hInstance = GetModuleHandleW(nullptr);
    wc.lpszClassName = L"ovl_dummy";
    RegisterClassExW(&wc);
    HWND hwnd = CreateWindowExW(0, wc.lpszClassName, L"", WS_OVERLAPPEDWINDOW, 0, 0, 8, 8,
                                nullptr, nullptr, wc.hInstance, nullptr);
    if (!hwnd) return false;

    DXGI_SWAP_CHAIN_DESC desc{};
    desc.BufferCount = 1;
    desc.BufferDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    desc.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    desc.OutputWindow = hwnd;
    desc.SampleDesc.Count = 1;
    desc.Windowed = TRUE;
    desc.SwapEffect = DXGI_SWAP_EFFECT_DISCARD;

    ID3D11Device* dev = nullptr;
    ID3D11DeviceContext* ctx = nullptr;
    IDXGISwapChain* sc = nullptr;
    D3D_FEATURE_LEVEL level{};
    HRESULT hr = D3D11CreateDeviceAndSwapChain(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0, nullptr, 0,
                                               D3D11_SDK_VERSION, &desc, &sc, &dev, &level, &ctx);
    if (FAILED(hr) || !sc) {
        DestroyWindow(hwnd);
        return false;
    }

    void** vtable = *reinterpret_cast<void***>(sc);
    g_present = reinterpret_cast<PresentFn>(vtable[8]);
    DWORD old = 0;
    VirtualProtect(vtable + 8, sizeof(void*), PAGE_READWRITE, &old);
    vtable[8] = reinterpret_cast<void*>(&HookPresent);
    VirtualProtect(vtable + 8, sizeof(void*), old, &old);

    sc->Release();
    ctx->Release();
    dev->Release();
    DestroyWindow(hwnd);
    return true;
}

DWORD WINAPI InitThread(LPVOID) {
    for (int i = 0; i < 300; ++i) {   // wait for the game's window
        if (GetForegroundWindow() || FindWindowW(nullptr, nullptr)) break;
        Sleep(100);
    }
    HookSwapChain();
    return 0;
}

}  // namespace

// zig's mingw libc imports these three from the private UCRT shim, which Windows
// does not ship. Provide local definitions instead.
extern "C" void* memchr(const void* s, int c, size_t n) {
    const unsigned char* p = static_cast<const unsigned char*>(s);
    for (size_t i = 0; i < n; ++i) if (p[i] == static_cast<unsigned char>(c)) return const_cast<void*>(static_cast<const void*>(p + i));
    return nullptr;
}
extern "C" char* strchr(const char* s, int c) {
    for (;; ++s) { if (*s == static_cast<char>(c)) return const_cast<char*>(s); if (!*s) return nullptr; }
}
extern "C" char* strstr(const char* h, const char* n) {
    if (!*n) return const_cast<char*>(h);
    for (; *h; ++h) { const char* a = h; const char* b = n; while (*a && *b && *a == *b) { ++a; ++b; } if (!*b) return const_cast<char*>(h); }
    return nullptr;
}

extern "C" int __cxa_guard_acquire(long long* g) { return !*g; }
extern "C" void __cxa_guard_release(long long* g) { *g = 1; }
extern "C" void __cxa_guard_abort(long long*) {}

void* operator new(size_t n) { return malloc(n); }
void* operator new[](size_t n) { return malloc(n); }
void operator delete(void* p) noexcept { free(p); }
void operator delete[](void* p) noexcept { free(p); }
void operator delete(void* p, size_t) noexcept { free(p); }
void operator delete[](void* p, size_t) noexcept { free(p); }

// Same chain the Frida travel probe uses: game global -> network manager ->
// local player controller, then FUN_14148FA60(pc, FString, 0, false, FGuid).

// Host: mirror the handler's own sequence -- GetWorld(menuObject, &out), take
// [out+8] as UWorld*, then FUN_1403a3050(world, hxString, 0, 0). The hxString
// argument is a Haxe string handle: an even pointer whose +0x18 is an FString.
extern "C" __declspec(dllexport) int ovl_host() {
    HMODULE base = GetModuleHandleW(nullptr);
    auto at = [base](uintptr_t off) { return reinterpret_cast<uintptr_t>(base) + off; };
    auto get_world = reinterpret_cast<void (*)(void*, void*)>(at(0x872890));
    unsigned char out[16] = {};
    void* world = nullptr;
    if (g_menu_self) {
        get_world(g_menu_self, out);
        void* wrapper = *reinterpret_cast<void**>(out);
        if (wrapper) world = *reinterpret_cast<void**>(reinterpret_cast<char*>(wrapper) + 8);
    }
    if (!world) {
        void* g = *reinterpret_cast<void**>(at(0x3C2BAA0));
        void* arr = g ? *reinterpret_cast<void**>(reinterpret_cast<uintptr_t>(g) + 0xB30) : nullptr;
        void* ctx = arr ? *reinterpret_cast<void**>(arr) : nullptr;
        void* nw = ctx ? *reinterpret_cast<void**>(reinterpret_cast<uintptr_t>(ctx) + 0x230) : nullptr;
        if (nw) {
            auto get_pc = reinterpret_cast<void* (*)(void*, int)>(at(0x16FA590));
            void* pc = get_pc(nw, 0);
            if (pc) {
                get_world(pc, out);
                void* wrapper = *reinterpret_cast<void**>(out);
                if (wrapper) world = *reinterpret_cast<void**>(reinterpret_cast<char*>(wrapper) + 8);
            }
        }
    }
    if (!world) return 0;

    wchar_t url[] = L"/Game/Maps/GameCon/GameCon_PersistentLevel?listen?game=Tag?IsHost=true&PlayerSessionID=host-1";
    struct HxStr { unsigned char pad[0x18]; wchar_t* data; int num; int max; };
    HxStr hs{};
    hs.data = url;
    hs.num = static_cast<int>(wcslen(url) + 1);
    hs.max = hs.num;
    auto travel = reinterpret_cast<void (*)(void*, uintptr_t, bool, bool)>(at(0x3A3050));
    travel(world, reinterpret_cast<uintptr_t>(&hs), true, false);
    return 1;
}

extern "C" __declspec(dllexport) int ovl_clicks() { return g_click_count; }
extern "C" __declspec(dllexport) void ovl_open(int which) { g_panel = which; g_visible = true; }
extern "C" __declspec(dllexport) int ovl_visible() { return g_visible ? 1 : 0; }

extern "C" __declspec(dllexport) int ovl_connect_index(int i) {
    if (i < 0 || i >= g_server_count) return 0;
    wchar_t waddr[256] = {};
    MultiByteToWideChar(CP_UTF8, 0, g_servers[i].addr, -1, waddr, 256);
    wcscat(waddr, L"?Version=HNS-release.2.7.31");
    return LocalTravel(waddr) ? 1 : 0;
}

extern "C" __declspec(dllexport) int ovl_connect(const wchar_t* url) {
    return LocalTravel(url) ? 1 : 0;
}

extern "C" __declspec(dllexport) void ovl_show(int v) {
    g_visible = (v != 0);
}

extern "C" __declspec(dllexport) int ovl_ready() {
    return g_imgui_ready ? 1 : 0;
}

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);
        GetModuleFileNameA(nullptr, g_game_dir, MAX_PATH);
        char* slash = strrchr(g_game_dir, '\\');
        if (slash) slash[1] = 0;
        CreateThread(nullptr, 0, InitThread, nullptr, 0, nullptr);
    }
    return TRUE;
}
