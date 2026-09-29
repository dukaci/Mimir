// mimir_tests: history, formatting, settings and the process reader.
// `mimir_tests --dump` prints one process snapshot (for comparisons with other tools).
#include <chrono>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <string>
#include <thread>
#include <vector>

#include "history.h"
#include "settings.h"
#include "sys.h"

#ifdef _WIN32
#include <windows.h>
#else
#include <sys/wait.h>
#include <unistd.h>
#endif

namespace {

int failures = 0;
#define CHECK(c)                                                                 \
    do {                                                                         \
        if (!(c)) {                                                              \
            std::fprintf(stderr, "%s:%d: CHECK(%s) failed\n", __FILE__, __LINE__, #c); \
            ++failures;                                                          \
        }                                                                        \
    } while (0)

struct Test {
    const char* name;
    void (*fn)();
};
std::vector<Test>& registry() {
    static std::vector<Test> t;
    return t;
}
#define TEST(name)                                                               \
    void name();                                                                 \
    const bool name##_registered = (registry().push_back({#name, name}), true);  \
    void name()

double now_s() { return double(std::time(nullptr)); }

struct Proc {
    uint32_t pid;
    const char* name;
    float cpu, mem;
};

Tick tick(History& h, double ts, std::vector<Proc> procs, float sys_cpu = 0) {
    Tick t{ts, {}, {}};
    for (auto& [pid, name, cpu, mem] : procs) {
        Row r{pid, h.intern(name), {}};
        r.v[CPU] = cpu;
        r.v[MEM] = mem;
        t.rows.push_back(r);
    }
    t.sys[CPU] = sys_cpu;
    return t;
}

std::vector<std::string> names(const History& h, const std::vector<EntityRow>& rows) {
    std::vector<std::string> out;
    for (auto& r : rows) out.push_back(h.name(r.name));
    return out;
}

TEST(rows_keep_every_value_and_store_only_nonzero_ones) {
    Row idle{7, 3, {}}, busy{9, (1u << 23) - 1, {}};
    idle.v[THREADS] = 1;
    for (int i = 0; i < kProcMetrics; ++i) busy.v[i] = float(i) + 0.5f;
    Rows rows;
    rows.push_back(idle);
    rows.push_back(busy);
    CHECK(rows.size() == 2 && rows.bytes() == 12 + 44);
    std::vector<Row> out;
    for (const Row& r : rows) out.push_back(r);
    CHECK(out.size() == 2 && std::memcmp(&out[0], &idle, sizeof idle) == 0 &&
          std::memcmp(&out[1], &busy, sizeof busy) == 0);
}

TEST(peaks_track_value_and_time_per_pid_and_name) {
    History h(100);
    double t0 = now_s() - 10;
    h.append(tick(h, t0, {{1, "a.exe", 5, 0}, {2, "a.exe", 1, 0}}));
    h.append(tick(h, t0 + 1, {{1, "a.exe", 2, 0}, {2, "a.exe", 9, 0}}));
    h.append(tick(h, t0 + 2, {{1, "a.exe", 3, 0}}));
    CHECK(h.peaks(pid_key(1))[CPU].v == 5 && h.peaks(pid_key(1))[CPU].ts == t0);
    CHECK(h.peaks(pid_key(2))[CPU].v == 9 && h.peaks(pid_key(2))[CPU].ts == t0 + 1);
    // a group's peak is the peak of the per-tick sum, not the sum of peaks
    Peak g = h.peaks(name_key(h.intern("a.exe")))[CPU];
    CHECK(g.v == 11 && g.ts == t0 + 1);
}

TEST(rankings_group_and_sort) {
    History h(100);
    double now = now_s();
    h.append(tick(h, now - 2, {{1, "a.exe", 10, 0}, {2, "b.exe", 1, 0}, {3, "a.exe", 4, 0}}));
    h.append(tick(h, now - 1, {{1, "a.exe", 2, 0}, {2, "b.exe", 6, 0}, {3, "a.exe", 4, 0}}));
    auto rows = rank(h.aggregate(60, true, true, "", 0, now), CPU, Field::Current, true, 50);
    CHECK((names(h, rows) == std::vector<std::string>{"a.exe", "b.exe"}));     // a = 2+4, b = 6: tie keeps order
    CHECK((rows[0].pids == std::vector<uint32_t>{1, 3}));
    CHECK(rows[0].cur[CPU] == 6 && rows[0].avg[CPU] == 10 && rows[0].max[CPU] == 14);

    auto pids = [](const std::vector<EntityRow>& rs) {
        std::vector<uint32_t> out;
        for (auto& r : rs) out.push_back(r.pids[0]);
        return out;
    };
    auto flat = h.aggregate(60, false, true, "", 0, now);
    CHECK((pids(rank(flat, CPU, Field::Average, true, 50)) == std::vector<uint32_t>{1, 3, 2}));   // avg 6, 4, 3.5
    CHECK((pids(rank(flat, CPU, Field::Maximum, false, 50)) == std::vector<uint32_t>{3, 2, 1}));  // max 4, 6, 10
}

TEST(rankings_filters_and_dead_processes) {
    History h(100);
    double now = now_s();
    h.append(tick(h, now - 2, {{1, "gone.exe", 50, 0}, {2, "System Idle Process", 90, 0}}));
    h.append(tick(h, now - 1, {{3, "keep.exe", 1, 0}, {2, "System Idle Process", 90, 0}}));
    auto rows = h.aggregate(60, false, true, "", 0, now);
    CHECK(rows.size() == 2);
    for (auto& r : rows) {
        std::string n = h.name(r.name);
        CHECK(n != "System Idle Process");
        if (n == "gone.exe") CHECK(!r.alive && r.cur[CPU] == 0);
        if (n == "keep.exe") CHECK(r.alive);
    }
    CHECK((names(h, h.aggregate(60, false, false, "IDLE", 0, now)) == std::vector<std::string>{"System Idle Process"}));
    CHECK((names(h, rank(h.aggregate(60, false, true, "", 0, now), CPU, Field::Current, true, 50, true)) ==
           std::vector<std::string>{"keep.exe"}));
}

TEST(series_and_window) {
    History h(100);
    double now = now_s();
    for (int i = 0; i < 5; ++i) h.append(tick(h, now - 4 + i, {{1, "a.exe", float(i), float(i * 10)}}, float(i * 2)));
    Metric m[2] = {CPU, MEM};
    std::vector<double> out[2];
    auto ts = h.entity_series(pid_key(1), {}, m, 2, 2.5, now, out);
    CHECK(ts.size() == 3);
    CHECK((out[0] == std::vector<double>{2, 3, 4}) && (out[1] == std::vector<double>{20, 30, 40}));
    h.system_series(m, 1, 100, now, out);
    CHECK((out[0] == std::vector<double>{0, 2, 4, 6, 8}));
    CHECK(h.system_peak(CPU).v == 8);
    CHECK(h.span() == 4);
    std::vector<std::vector<double>> multi;
    auto rows = h.aggregate(60, true, true, "", 0, now);
    std::vector<const EntityRow*> ents{&rows[0]};
    CHECK(h.multi_series(ents, MEM, 100, now, multi).size() == 5 && multi[0][4] == 40);
}

TEST(maxlen_and_clear) {
    History h(3);
    double now = now_s();
    for (int i = 0; i < 6; ++i) h.append(tick(h, now + i, {{1, "a.exe", float(i), 0}}));
    CHECK(h.size() == 3);
    h.set_maxlen(10);
    CHECK(h.size() == 3);
    h.clear();
    CHECK(h.size() == 0 && h.peaks(pid_key(1))[CPU].ts == 0);
}

TEST(current_cpu_is_averaged_over_smoothing_window) {
    History h(100);
    double now = now_s();
    float a[] = {0, 0, 0, 0, 50};                  // a.exe spikes on the last sample; b.exe is steady
    for (int i = 0; i < 5; ++i) h.append(tick(h, now - 4 + i, {{1, "a.exe", a[i], 0}, {2, "b.exe", 20, 0}}));
    auto raw = rank(h.aggregate(30, true, true, "", 0, now), CPU, Field::Current, true, 50);
    CHECK(raw[0].cur[CPU] == 50 && raw[1].cur[CPU] == 20);
    auto rows = rank(h.aggregate(30, true, true, "", 5, now), CPU, Field::Current, true, 50);
    CHECK((names(h, rows) == std::vector<std::string>{"b.exe", "a.exe"}));     // the spike no longer jumps up
    CHECK(rows[0].cur[CPU] == 20 && rows[1].cur[CPU] == 10);
}

TEST(formatting) {
    CHECK(format_bytes(0) == "0 B");
    CHECK(format_bytes(1536) == "1.5 KB");
    CHECK(format_value(Kind::Rate, 3.5 * 1024 * 1024) == "3.5 MB/s");
    CHECK(format_value(Kind::Percent, 12.345) == "12.3%");
}

TEST(settings_round_trip) {
    auto path = (std::filesystem::temp_directory_path() / "mimir_test_settings.json").string();
    Settings s;
    s.sample_interval = 0.5;
    s.sort_field = "peak";
    s.group_by_name = false;
    s.history_seconds = 99999999;                  // clamped by validate()
    s.save(path);
    Settings l = Settings::load(path);
    std::filesystem::remove(path);
    CHECK(l.sample_interval == 0.5 && l.sort_field == "peak" && !l.group_by_name && l.history_seconds == 24 * 3600);
}

TEST(process_reader_sees_itself) {
    ProcessReader reader;
    const ProcInfo* me = nullptr;
    uint32_t self = 0;
#ifdef _WIN32
    self = GetCurrentProcessId();
#else
    self = uint32_t(getpid());
#endif
    for (auto& p : reader.read())
        if (p.pid == self) me = &p;
    CHECK(me != nullptr);
    if (!me) return;
    CHECK(me->name.find("mimir_tests") == 0);
    CHECK(me->threads >= 1 && me->mem > 0 && me->cpu_time >= 0);
    SystemCounters c = read_system();
    CHECK(c.mem_total > c.mem_used && c.mem_used > 0 && c.cpu_total > 0);
}

#ifndef _WIN32
pid_t spawn(const std::string& exe) {
    pid_t pid = fork();
    if (pid == 0) {
        execl(exe.c_str(), exe.c_str(), "30", nullptr);
        _exit(127);
    }
    return pid;
}

TEST(long_name_comes_from_cmdline_and_kill_ends_it) {
    auto dir = std::filesystem::temp_directory_path() / "mimir_test_bin";
    std::filesystem::create_directories(dir);
    auto exe = dir / "mimir_long_name_test";
    std::filesystem::copy_file("/bin/sleep", exe, std::filesystem::copy_options::overwrite_existing);
    pid_t pid = spawn(exe.string());
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
    ProcessReader reader;
    std::string name;
    for (auto& p : reader.read())
        if (p.pid == uint32_t(pid)) name = p.name;
    CHECK(name == "mimir_long_name_test");
    KillReport r = kill_processes({uint32_t(pid)}, true);
    CHECK(r.killed.size() == 1 && r.failed.empty());
    waitpid(pid, nullptr, 0);
    std::filesystem::remove_all(dir);
}
#endif

int dump() {
    ProcessReader reader;
    for (auto& p : reader.read())
        std::printf("%u\t%s\t%u\t%llu\t%.2f\t%llu\t%llu\n", p.pid, p.name.c_str(), p.threads,
                    (unsigned long long)p.mem, p.cpu_time, (unsigned long long)p.read_bytes,
                    (unsigned long long)p.write_bytes);
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc > 1 && std::strcmp(argv[1], "--dump") == 0) return dump();
    for (auto& t : registry()) {
        int before = failures;
        t.fn();
        std::printf("%-55s %s\n", t.name, failures == before ? "ok" : "FAILED");
    }
    std::printf("%zu tests, %d failed checks\n", registry().size(), failures);
    return failures ? 1 : 0;
}
