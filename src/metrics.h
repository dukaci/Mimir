// Metric definitions and value formatting shared by the sampler, history and UI.
#pragma once

#include <chrono>
#include <cstdint>
#include <cstdio>
#include <ctime>
#include <string>

// Colour in Dear ImGui's ImU32 layout (0xAABBGGRR), so this header needs no ImGui.
constexpr uint32_t rgb(uint8_t r, uint8_t g, uint8_t b, uint8_t a = 255) {
    return uint32_t(a) << 24 | uint32_t(b) << 16 | uint32_t(g) << 8 | r;
}

enum class Kind : uint8_t { Percent, Bytes, Rate, Count };

// Per-process metrics, in the order of Row::v. The first kSysMetrics are also the system metrics.
enum Metric : int { CPU, MEM, DISK_R, DISK_W, NET_D, NET_U, GPU, VRAM, THREADS, kProcMetrics };
constexpr int kSysMetrics = THREADS;

struct MetricInfo {
    const char* id;          // settings.json and CSV key
    const char* label;
    const char* header;      // table column
    Kind kind;
    uint32_t color;
};

// Catppuccin Mocha accents, one per metric
inline constexpr MetricInfo kMetrics[kProcMetrics] = {
    {"cpu", "CPU", "CPU", Kind::Percent, rgb(137, 180, 250)},
    {"mem", "Memory", "Memory", Kind::Bytes, rgb(203, 166, 247)},
    {"disk_read", "Disk read", "Disk R", Kind::Rate, rgb(166, 227, 161)},
    {"disk_write", "Disk write", "Disk W", Kind::Rate, rgb(250, 179, 135)},
    {"net_down", "Download", "Down", Kind::Rate, rgb(148, 226, 213)},
    {"net_up", "Upload", "Up", Kind::Rate, rgb(245, 194, 231)},
    {"gpu", "GPU", "GPU", Kind::Percent, rgb(249, 226, 175)},
    {"gpu_mem", "VRAM", "VRAM", Kind::Bytes, rgb(180, 190, 254)},
    {"threads", "Threads", "Threads", Kind::Count, rgb(137, 220, 235)},
};

// A resource as the tiles and the chart show it: one or two metrics. Metrics of
// different kinds (GPU % and VRAM) get a plot each.
struct Group {
    const char* id;
    const char* label;
    Metric metrics[2];
    int count;
};

inline constexpr Group kGroups[] = {
    {"cpu", "CPU", {CPU, CPU}, 1},
    {"mem", "Memory", {MEM, MEM}, 1},
    {"disk", "Disk", {DISK_R, DISK_W}, 2},
    {"net", "Network", {NET_D, NET_U}, 2},
    {"gpu", "GPU", {GPU, VRAM}, 2},
};
constexpr int kGroupCount = int(sizeof kGroups / sizeof kGroups[0]);

inline std::string format_bytes(double v, int decimals = 1) {
    static const char* units[] = {"B", "KB", "MB", "GB", "TB"};
    int u = 0;
    while (v >= 1024 && u < 4) {
        v /= 1024;
        ++u;
    }
    char buf[32];
    if (u == 0)
        std::snprintf(buf, sizeof buf, "%.0f B", v);
    else
        std::snprintf(buf, sizeof buf, "%.*f %s", decimals, v, units[u]);
    return buf;
}

inline std::string format_value(Kind kind, double v) {
    char buf[32];
    switch (kind) {
    case Kind::Percent: std::snprintf(buf, sizeof buf, "%.1f%%", v); return buf;
    case Kind::Bytes: return format_bytes(v);
    case Kind::Rate: return format_bytes(v) + "/s";
    case Kind::Count: std::snprintf(buf, sizeof buf, "%.0f", v); return buf;
    }
    return {};
}

// The chart axis reads in these units
inline const char* axis_unit(Kind kind) {
    switch (kind) {
    case Kind::Percent: return "%";
    case Kind::Bytes: return "MB";
    case Kind::Rate: return "KB/s";
    default: return "";
    }
}

inline double axis_scale(Kind kind) {
    switch (kind) {
    case Kind::Bytes: return 1.0 / (1024 * 1024);
    case Kind::Rate: return 1.0 / 1024;
    default: return 1.0;
    }
}

inline double wall_now() {                     // epoch seconds
    using namespace std::chrono;
    return duration<double>(system_clock::now().time_since_epoch()).count();
}

inline std::string format_clock(double ts, const char* fmt = "%H:%M:%S") {
    std::time_t t = std::time_t(ts);
    std::tm tm{};
#ifdef _WIN32
    localtime_s(&tm, &t);
#else
    localtime_r(&t, &tm);
#endif
    char buf[32];
    std::strftime(buf, sizeof buf, fmt, &tm);
    return buf;
}
