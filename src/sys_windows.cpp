// Windows: one NtQuerySystemInformation call for every process (what Task Manager uses:
// no per-process handles, so protected processes are included), system counters,
// termination, elevation and the themed title bar.
#include "sys.h"

#include <winsock2.h>          // before windows.h, and with ws2ipdef.h, so iphlpapi.h declares GetIfTable2
#include <ws2ipdef.h>
#include <windows.h>
#include <winternl.h>
#include <dwmapi.h>
#include <iphlpapi.h>
#include <shellapi.h>
#include <winioctl.h>

#include <filesystem>
#include <string>

namespace {

// SYSTEM_PROCESS_INFORMATION as the kernel fills it (winternl.h hides most fields)
struct ProcessEntry {
    ULONG NextEntryOffset, NumberOfThreads;
    LARGE_INTEGER WorkingSetPrivateSize;
    ULONG HardFaultCount, NumberOfThreadsHighWatermark;
    ULONGLONG CycleTime;
    LARGE_INTEGER CreateTime, UserTime, KernelTime;
    UNICODE_STRING ImageName;
    LONG BasePriority;
    HANDLE UniqueProcessId, InheritedFromUniqueProcessId;
    ULONG HandleCount, SessionId;
    ULONG_PTR UniqueProcessKey;
    SIZE_T PeakVirtualSize, VirtualSize;
    ULONG PageFaultCount;
    SIZE_T PeakWorkingSetSize, WorkingSetSize, QuotaPeakPagedPoolUsage, QuotaPagedPoolUsage,
        QuotaPeakNonPagedPoolUsage, QuotaNonPagedPoolUsage, PagefileUsage, PeakPagefileUsage, PrivatePageCount;
    LARGE_INTEGER ReadOperationCount, WriteOperationCount, OtherOperationCount, ReadTransferCount,
        WriteTransferCount, OtherTransferCount;
};
constexpr NTSTATUS kLengthMismatch = NTSTATUS(0xC0000004L);

std::string utf8(const wchar_t* s, int len) {
    int n = WideCharToMultiByte(CP_UTF8, 0, s, len, nullptr, 0, nullptr, nullptr);
    std::string out(size_t(n), '\0');
    WideCharToMultiByte(CP_UTF8, 0, s, len, out.data(), n, nullptr, nullptr);
    return out;
}

double seconds(const FILETIME& f) { return double(uint64_t(f.dwHighDateTime) << 32 | f.dwLowDateTime) / 1e7; }

bool elevated() {
    HANDLE token;
    TOKEN_ELEVATION e{};
    DWORD size = 0;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) return false;
    GetTokenInformation(token, TokenElevation, &e, sizeof e, &size);
    CloseHandle(token);
    return e.TokenIsElevated != 0;
}

// Lets an elevated process open processes of other users and services
void enable_debug_privilege() {
    static bool done = false;
    if (done) return;
    done = true;
    HANDLE token;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, &token)) return;
    TOKEN_PRIVILEGES tp{1, {{{0, 0}, SE_PRIVILEGE_ENABLED}}};
    if (LookupPrivilegeValueW(nullptr, SE_DEBUG_NAME, &tp.Privileges[0].Luid))
        AdjustTokenPrivileges(token, FALSE, &tp, 0, nullptr, nullptr);
    CloseHandle(token);
}

}  // namespace

struct ProcessReader::State {
    std::vector<unsigned char> buf = std::vector<unsigned char>(1 << 20);
    std::vector<ProcInfo> out;
};

ProcessReader::ProcessReader() : s_(std::make_unique<State>()) {}
ProcessReader::~ProcessReader() = default;

const std::vector<ProcInfo>& ProcessReader::read() {
    State& s = *s_;
    s.out.clear();
    ULONG need = 0;
    NTSTATUS st;
    while ((st = NtQuerySystemInformation(SYSTEM_INFORMATION_CLASS(5 /* SystemProcessInformation */), s.buf.data(),
                                          ULONG(s.buf.size()), &need)) == kLengthMismatch)
        s.buf.resize(need + (1 << 16));
    if (st != 0) return s.out;
    for (size_t off = 0;;) {
        auto* p = reinterpret_cast<const ProcessEntry*>(s.buf.data() + off);
        uint32_t pid = uint32_t(uintptr_t(p->UniqueProcessId));
        std::string name = p->ImageName.Buffer && p->ImageName.Length
                               ? utf8(p->ImageName.Buffer, p->ImageName.Length / 2)
                               : pid == 0 ? "System Idle Process" : "pid " + std::to_string(pid);
        s.out.push_back({pid, uint32_t(uintptr_t(p->InheritedFromUniqueProcessId)), std::move(name),
                         double(p->UserTime.QuadPart + p->KernelTime.QuadPart) / 1e7,
                         uint64_t(p->WorkingSetPrivateSize.QuadPart), uint64_t(p->ReadTransferCount.QuadPart),
                         uint64_t(p->WriteTransferCount.QuadPart), p->NumberOfThreads});
        if (!p->NextEntryOffset || off + p->NextEntryOffset + sizeof(ProcessEntry) > s.buf.size()) break;
        off += p->NextEntryOffset;
    }
    return s.out;
}

SystemCounters read_system() {
    SystemCounters c{};
    FILETIME idle, kernel, user;
    if (GetSystemTimes(&idle, &kernel, &user)) {           // kernel time includes idle time
        c.cpu_total = seconds(kernel) + seconds(user);
        c.cpu_busy = c.cpu_total - seconds(idle);
    }
    MEMORYSTATUSEX m{sizeof m};
    if (GlobalMemoryStatusEx(&m)) {
        c.mem_total = m.ullTotalPhys;
        c.mem_used = m.ullTotalPhys - m.ullAvailPhys;
    }
    for (int i = 0; i < 32; ++i) {                          // physical disks
        std::wstring path = L"\\\\.\\PhysicalDrive" + std::to_wstring(i);
        HANDLE h = CreateFileW(path.c_str(), 0, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_EXISTING, 0, nullptr);
        if (h == INVALID_HANDLE_VALUE) break;
        DISK_PERFORMANCE perf{};
        DWORD got = 0;
        if (DeviceIoControl(h, IOCTL_DISK_PERFORMANCE, nullptr, 0, &perf, sizeof perf, &got, nullptr)) {
            c.disk_read += uint64_t(perf.BytesRead.QuadPart);
            c.disk_write += uint64_t(perf.BytesWritten.QuadPart);
        }
        CloseHandle(h);
    }
    MIB_IF_TABLE2* table = nullptr;                         // hardware NICs: VPNs and loopback repeat their traffic
    if (GetIfTable2(&table) == NO_ERROR) {
        for (ULONG i = 0; i < table->NumEntries; ++i) {
            const MIB_IF_ROW2& r = table->Table[i];
            if (!r.InterfaceAndOperStatusFlags.HardwareInterface || r.InterfaceAndOperStatusFlags.FilterInterface) continue;
            c.net_recv += r.InOctets;
            c.net_sent += r.OutOctets;
        }
        FreeMibTable(table);
    }
    return c;
}

int cpu_count() {
    DWORD n = GetActiveProcessorCount(ALL_PROCESSOR_GROUPS);
    return n ? int(n) : 1;
}

KillReport kill_processes(const std::vector<uint32_t>& pids, bool children) {
    KillReport r;
    enable_debug_privilege();
    ProcessReader reader;
    std::vector<std::pair<uint32_t, HANDLE>> ended;
    for (uint32_t pid : kill_order(reader.read(), pids, children)) {
        if (pid == GetCurrentProcessId()) {
            r.skipped_self = true;
            continue;
        }
        HANDLE h = OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE, FALSE, pid);
        if (!h) {
            DWORD e = GetLastError();
            if (e == ERROR_INVALID_PARAMETER)
                r.killed.push_back(pid);                   // already gone
            else
                r.failed.push_back({pid, e == ERROR_ACCESS_DENIED ? "access denied" : "error " + std::to_string(e)});
            continue;
        }
        if (TerminateProcess(h, 1))
            ended.push_back({pid, h});
        else {
            DWORD e = GetLastError();
            r.failed.push_back({pid, e == ERROR_ACCESS_DENIED ? "access denied" : "error " + std::to_string(e)});
            CloseHandle(h);
        }
    }
    ULONGLONG deadline = GetTickCount64() + 2000;             // up to 2 s for the kernel to finish them
    for (auto& [pid, h] : ended) {
        ULONGLONG now = GetTickCount64();
        DWORD wait = now < deadline ? DWORD(deadline - now) : 0;
        if (WaitForSingleObject(h, wait) == WAIT_OBJECT_0)
            r.killed.push_back(pid);
        else
            r.failed.push_back({pid, "still running"});
        CloseHandle(h);
    }
    return r;
}

bool can_capture() { return elevated(); }

std::string capture_hint() { return "Restart as administrator to attribute network traffic to processes."; }

bool can_elevate() { return !elevated(); }

bool relaunch_elevated(int argc, char** argv) {
    wchar_t exe[MAX_PATH];
    GetModuleFileNameW(nullptr, exe, MAX_PATH);
    std::wstring args;
    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        args += L" \"" + std::wstring(a.begin(), a.end()) + L"\"";
    }
    return reinterpret_cast<INT_PTR>(ShellExecuteW(nullptr, L"runas", exe, args.c_str(), nullptr, SW_SHOWNORMAL)) > 32;
}

std::string app_dir() {
    wchar_t exe[MAX_PATH];
    GetModuleFileNameW(nullptr, exe, MAX_PATH);
    return std::filesystem::path(exe).parent_path().parent_path().string();
}

std::pair<std::string, std::string> ui_fonts() {
    wchar_t dir[MAX_PATH];
    GetWindowsDirectoryW(dir, MAX_PATH);
    auto fonts = std::filesystem::path(dir) / "Fonts";
    return {(fonts / "segoeui.ttf").string(), (fonts / "seguisb.ttf").string()};
}

void style_window(void* native) {
    HWND hwnd = HWND(native);
    if (!hwnd) return;
    auto set = [&](DWORD attr, DWORD value) { DwmSetWindowAttribute(hwnd, attr, &value, sizeof value); };
    auto bgr = [](uint8_t r, uint8_t g, uint8_t b) { return DWORD(r) | DWORD(g) << 8 | DWORD(b) << 16; };
    set(20, 1);                                  // DWMWA_USE_IMMERSIVE_DARK_MODE
    set(35, bgr(24, 24, 37));                    // DWMWA_CAPTION_COLOR (Windows 11): theme MANTLE
    set(36, bgr(205, 214, 244));                 // DWMWA_TEXT_COLOR: theme TEXT
    set(34, bgr(49, 50, 68));                    // DWMWA_BORDER_COLOR: theme SURFACE0
    set(33, 2);                                  // DWMWA_WINDOW_CORNER_PREFERENCE: round
}

bool window_alive(void* native) { return !native || IsWindow(HWND(native)); }
