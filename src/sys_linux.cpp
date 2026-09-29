// Linux: /proc and /sys readers, process termination, rights checks.
//
// Two reads per process, /proc/<pid>/stat (CPU time, threads, resident memory)
// and /proc/<pid>/io (disk bytes), give the counters psutil reads from five files.
#include "sys.h"

#include <dirent.h>
#include <fcntl.h>
#include <signal.h>
#include <unistd.h>

#include <cerrno>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <string_view>
#include <thread>
#include <unordered_map>

namespace fs = std::filesystem;

namespace {

// File contents into buf (NUL-terminated), or 0 when the process is gone or access is denied.
size_t read_file(const char* path, char* buf, size_t size) {
    int fd = ::open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return 0;
    ssize_t n = ::read(fd, buf, size - 1);
    ::close(fd);
    if (n <= 0) return 0;
    buf[n] = 0;
    return size_t(n);
}

uint64_t read_u64(const char* path) {
    char buf[64];
    return read_file(path, buf, sizeof buf) ? std::strtoull(buf, nullptr, 10) : 0;
}

// Value after `key` in a "key value" text such as /proc/<pid>/io or /proc/meminfo
uint64_t field(const char* text, const char* key) {
    const char* p = std::strstr(text, key);
    return p ? std::strtoull(p + std::strlen(key), nullptr, 10) : 0;
}

// comm is cut to 15 bytes. Like psutil, take the name from cmdline when it starts the same.
std::string full_name(const char* pid, const std::string& comm) {
    char path[64], buf[4096];
    std::snprintf(path, sizeof path, "/proc/%s/cmdline", pid);
    size_t n = read_file(path, buf, sizeof buf);
    if (!n) return comm;
    std::string data(buf, n);
    // psutil's rule: args end in NUL, but setproctitle() users (Chrome) separate them with spaces
    char sep = data.back() == '\0' ? '\0' : ' ';
    if (data.back() == sep) data.pop_back();
    std::string arg0 = data.substr(0, data.find(sep));
    if (sep == '\0' && data.find('\0') == std::string::npos && data.find(' ') != std::string::npos)
        arg0 = data.substr(0, data.find(' '));
    std::string exe = arg0.substr(arg0.rfind('/') + 1);
    return exe.rfind(comm, 0) == 0 ? exe : comm;
}

bool alive(uint32_t pid) {             // a zombie has ended; only its parent's wait() is missing
    char path[64], buf[512];
    std::snprintf(path, sizeof path, "/proc/%u/stat", pid);
    if (!read_file(path, buf, sizeof buf)) return false;
    const char* p = std::strrchr(buf, ')');
    return p && p[2] != 'Z';
}

}  // namespace

struct ProcessReader::State {
    // A name is looked up once per process: it is redone when the pid is reused
    // (start time) or the process renames itself (comm).
    struct Name {
        uint64_t start;
        std::string comm, name;
        uint64_t seen;
    };
    std::unordered_map<uint32_t, Name> names;
    uint64_t pass = 0;
    std::vector<ProcInfo> out;
    double hz = double(sysconf(_SC_CLK_TCK));
    uint64_t page = uint64_t(sysconf(_SC_PAGESIZE));
};

ProcessReader::ProcessReader() : s_(std::make_unique<State>()) {}
ProcessReader::~ProcessReader() = default;

const std::vector<ProcInfo>& ProcessReader::read() {
    State& s = *s_;
    s.out.clear();
    ++s.pass;
    DIR* dir = opendir("/proc");
    if (!dir) return s.out;
    char path[64], buf[1024], io[512];
    while (dirent* e = readdir(dir)) {
        if (e->d_name[0] < '1' || e->d_name[0] > '9') continue;
        std::snprintf(path, sizeof path, "/proc/%s/stat", e->d_name);
        if (!read_file(path, buf, sizeof buf)) continue;
        char* open = std::strchr(buf, '(');
        char* close = std::strrchr(buf, ')');
        if (!open || !close) continue;
        // fields after "pid (comm) ": f[0] is field 3 (state) of proc(5)
        const char* f[22];
        int n = 0;
        for (char* p = close + 2; *p && n < 22; ++n) {
            f[n] = p;
            while (*p && *p != ' ') ++p;
            if (*p) *p++ = 0;
        }
        if (n < 22) continue;
        uint32_t pid = uint32_t(std::atoi(e->d_name));
        std::string_view comm(open + 1, size_t(close - open - 1));
        uint64_t start = std::strtoull(f[19], nullptr, 10);
        State::Name& nm = s.names[pid];
        if (!nm.seen || nm.start != start || nm.comm != comm) {
            nm.start = start;
            nm.comm = comm;
            nm.name = comm.size() >= 15 ? full_name(e->d_name, nm.comm) : nm.comm;
        }
        nm.seen = s.pass;
        std::snprintf(path, sizeof path, "/proc/%s/io", e->d_name);
        bool has_io = read_file(path, io, sizeof io);
        s.out.push_back(ProcInfo{pid, uint32_t(std::atoi(f[1])), nm.name,
                                 double(std::strtoull(f[11], nullptr, 10) + std::strtoull(f[12], nullptr, 10)) / s.hz,
                                 std::strtoull(f[21], nullptr, 10) * s.page,
                                 has_io ? field(io, "read_bytes:") : 0, has_io ? field(io, "write_bytes:") : 0,
                                 uint32_t(std::atoi(f[17]))});
    }
    closedir(dir);
    std::erase_if(s.names, [&](auto& kv) { return kv.second.seen != s.pass; });
    return s.out;
}

SystemCounters read_system() {
    SystemCounters c{};
    char buf[4096];
    if (read_file("/proc/stat", buf, sizeof buf)) {      // cpu user nice system idle iowait irq softirq steal
        uint64_t v[8] = {};
        std::sscanf(buf, "cpu %lu %lu %lu %lu %lu %lu %lu %lu", &v[0], &v[1], &v[2], &v[3], &v[4], &v[5], &v[6],
                    &v[7]);
        for (uint64_t x : v) c.cpu_total += double(x);
        c.cpu_busy = c.cpu_total - double(v[3] + v[4]);
    }
    if (read_file("/proc/meminfo", buf, sizeof buf)) {
        c.mem_total = field(buf, "MemTotal:") * 1024;
        c.mem_used = c.mem_total - field(buf, "MemAvailable:") * 1024;
    }
    std::error_code ec;
    // Only devices backed by hardware: dm/LUKS, loop and bridges repeat what a real disk or NIC carries.
    for (auto& d : fs::directory_iterator("/sys/block", ec)) {
        if (!fs::exists(d.path() / "device", ec)) continue;
        if (!read_file((d.path() / "stat").c_str(), buf, sizeof buf)) continue;
        uint64_t v[7] = {};
        std::sscanf(buf, "%lu %lu %lu %lu %lu %lu %lu", &v[0], &v[1], &v[2], &v[3], &v[4], &v[5], &v[6]);
        c.disk_read += v[2] * 512;                        // sectors are always 512 bytes here
        c.disk_write += v[6] * 512;
    }
    for (auto& d : fs::directory_iterator("/sys/class/net", ec)) {
        if (!fs::exists(d.path() / "device", ec)) continue;
        c.net_recv += read_u64((d.path() / "statistics/rx_bytes").c_str());
        c.net_sent += read_u64((d.path() / "statistics/tx_bytes").c_str());
    }
    return c;
}

int cpu_count() {
    long n = sysconf(_SC_NPROCESSORS_ONLN);
    return n > 0 ? int(n) : 1;
}

KillReport kill_processes(const std::vector<uint32_t>& pids, bool children) {
    KillReport r;
    ProcessReader reader;
    std::vector<uint32_t> sent;
    for (uint32_t pid : kill_order(reader.read(), pids, children)) {
        if (pid == uint32_t(getpid())) {
            r.skipped_self = true;
        } else if (::kill(pid_t(pid), SIGKILL) == 0 || errno == ESRCH) {
            sent.push_back(pid);
        } else {
            r.failed.push_back({pid, errno == EPERM ? "access denied" : std::strerror(errno)});
        }
    }
    for (int i = 0; i < 20; ++i) {                        // up to 2 s for the kernel to finish them
        bool any = false;
        for (uint32_t pid : sent) any |= alive(pid);
        if (!any) break;
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    for (uint32_t pid : sent) {
        if (alive(pid))
            r.failed.push_back({pid, "still running"});
        else
            r.killed.push_back(pid);
    }
    return r;
}

bool can_capture() {
    if (geteuid() == 0) return true;
    char buf[4096];
    if (!read_file("/proc/self/status", buf, sizeof buf)) return false;
    const char* p = std::strstr(buf, "CapEff:");
    return p && (std::strtoull(p + 7, nullptr, 16) >> 13 & 1);     // bit 13 = CAP_NET_RAW
}

std::string capture_hint() {
    return "Run ./build.sh (grants CAP_NET_RAW to bin/mimir) to attribute network traffic to processes.";
}

bool can_elevate() { return false; }
bool relaunch_elevated(int, char**) { return false; }

std::string app_dir() {
    std::error_code ec;
    fs::path exe = fs::read_symlink("/proc/self/exe", ec);
    return ec ? std::string(".") : exe.parent_path().parent_path().string();
}

std::pair<std::string, std::string> ui_fonts() {
    // Noto Sans last: it has no arrows or dots (the UI uses them). DejaVu has them but runs wide.
    static const char* pairs[][2] = {{"NimbusSans-Regular.otf", "NimbusSans-Bold.otf"},
                                     {"DejaVuSans.ttf", "DejaVuSans-Bold.ttf"},
                                     {"NotoSans-Regular.ttf", "NotoSans-SemiBold.ttf"},
                                     {"Ubuntu-R.ttf", "Ubuntu-M.ttf"}};
    std::unordered_map<std::string, std::string> found;
    std::error_code ec;
    const char* home = std::getenv("HOME");
    for (fs::path root : {fs::path("/usr/share/fonts"), fs::path("/usr/local/share/fonts"),
                          fs::path(home ? home : "/") / ".fonts"})
        for (auto it = fs::recursive_directory_iterator(root, ec); !ec && it != fs::end(it); it.increment(ec))
            found.try_emplace(it->path().filename().string(), it->path().string());
    for (auto& [regular, bold] : pairs) {
        auto r = found.find(regular);
        if (r == found.end()) continue;
        auto b = found.find(bold);
        return {r->second, b != found.end() ? b->second : r->second};
    }
    return {};
}

void style_window(void*) {}
bool window_alive(void*) { return true; }
