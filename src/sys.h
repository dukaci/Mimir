// Operating-system access: one interface, implemented in sys_linux.cpp and sys_windows.cpp.
#pragma once

#include <algorithm>
#include <cstdint>
#include <memory>
#include <string>
#include <utility>
#include <vector>

struct ProcInfo {
    uint32_t pid, ppid;
    std::string name;
    double cpu_time;          // seconds of user + kernel time, cumulative
    uint64_t mem;             // private working set (Windows) / resident set (Linux), bytes
    uint64_t read_bytes, write_bytes;   // cumulative disk I/O
    uint32_t threads;
};

// Every process in one pass: NtQuerySystemInformation on Windows, /proc on Linux.
class ProcessReader {
public:
    ProcessReader();
    ~ProcessReader();
    const std::vector<ProcInfo>& read();

private:
    struct State;
    std::unique_ptr<State> s_;
};

struct SystemCounters {
    double cpu_busy, cpu_total;         // cumulative, any unit
    uint64_t mem_used, mem_total;       // used = total - available
    uint64_t disk_read, disk_write;     // physical disks, cumulative bytes
    uint64_t net_recv, net_sent;        // physical NICs, cumulative bytes
};
SystemCounters read_system();
int cpu_count();

struct KillReport {
    std::vector<uint32_t> killed;
    std::vector<std::pair<uint32_t, std::string>> failed;
    bool skipped_self = false;
    std::string summary() const;
};
// Ends the processes (their descendants first, deepest first, when `children`).
KillReport kill_processes(const std::vector<uint32_t>& pids, bool children);

bool can_capture();                    // rights for per-process network attribution
std::string capture_hint();            // how to get them
bool can_elevate();                    // Windows, not elevated
bool relaunch_elevated(int argc, char** argv);
std::string app_dir();                 // settings.json, logs/, exports/ live here: the parent of bin/
std::pair<std::string, std::string> ui_fonts();   // regular + bold font files, empty when none found
void style_window(void* native);       // themed title bar (Windows 11)
bool window_alive(void* native);       // false once a host (Dashpan) destroyed our window

// The kill targets, descendants first and deepest first, so no parent can restart a child.
inline std::vector<uint32_t> kill_order(const std::vector<ProcInfo>& procs, const std::vector<uint32_t>& pids,
                                        bool children) {
    std::vector<std::pair<uint32_t, int>> found;          // pid, depth below a requested process
    for (uint32_t pid : pids)
        for (const ProcInfo& p : procs)
            if (p.pid == pid) found.push_back({pid, 0});
    for (size_t i = 0; children && i < found.size(); ++i)
        for (const ProcInfo& p : procs)
            if (p.ppid == found[i].first && p.pid != p.ppid) {
                bool seen = false;
                for (auto& f : found) seen |= f.first == p.pid;
                if (!seen) found.push_back({p.pid, found[i].second + 1});
            }
    std::stable_sort(found.begin(), found.end(), [](auto& a, auto& b) { return a.second > b.second; });
    std::vector<uint32_t> out;
    for (auto& f : found) out.push_back(f.first);
    return out;
}

inline std::string KillReport::summary() const {
    size_t n = killed.size();
    std::string s = "ended " + std::to_string(n) + (n == 1 ? " process" : " processes");
    if (!failed.empty()) {
        s += "; could not end " + std::to_string(failed.size()) + ":";
        for (size_t i = 0; i < failed.size() && i < 3; ++i)
            s += (i ? ", " : " ") + std::to_string(failed[i].first) + " (" + failed[i].second + ")";
        if (failed.size() > 3) s += " +" + std::to_string(failed.size() - 3) + " more";
    }
    if (skipped_self) s += "; skipped Mimir itself";
    return s;
}
