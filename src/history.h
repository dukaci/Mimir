// Thread-safe time series of process and system samples, with all-time peaks.
//
// One Tick per sample holds a Row per process, packed to 12-16 bytes for an
// idle process. An entity is one PID or, when grouped, every process with the
// same name.
#pragma once

#include <array>
#include <bit>
#include <cstdint>
#include <deque>
#include <mutex>
#include <optional>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

#include "metrics.h"

using Values = std::array<float, kProcMetrics>;
using SysValues = std::array<float, kSysMetrics>;

struct Row {
    uint32_t pid;
    uint32_t name;           // History::intern() id
    float v[kProcMetrics];
};

// The rows of one tick. Each row is stored as its PID, its name id with a mask of
// the nonzero metrics in the top bits, and then only those values. Most values
// are zero, so a row takes 12-20 bytes instead of 44. Iteration unpacks each row.
class Rows {
    static constexpr int kNameBits = 32 - kProcMetrics;     // name ids stay below 2^23
    static constexpr uint32_t kNameMask = (1u << kNameBits) - 1;

public:
    struct iterator {
        const uint32_t* p;
        Row operator*() const {
            Row r{p[0], p[1] & kNameMask, {}};
            const uint32_t* v = p + 2;
            for (uint32_t m = p[1] >> kNameBits, i = 0; m; m >>= 1, ++i)
                if (m & 1) r.v[i] = std::bit_cast<float>(*v++);
            return r;
        }
        iterator& operator++() {
            p += 2 + std::popcount(p[1] >> kNameBits);
            return *this;
        }
        bool operator!=(const iterator& o) const { return p != o.p; }
    };

    void push_back(const Row& r) {
        uint32_t mask = 0;
        for (int i = 0; i < kProcMetrics; ++i)
            if (r.v[i] != 0) mask |= 1u << i;
        words_.push_back(r.pid);
        words_.push_back(r.name | mask << kNameBits);
        for (int i = 0; i < kProcMetrics; ++i)
            if (mask >> i & 1) words_.push_back(std::bit_cast<uint32_t>(r.v[i]));
        ++count_;
    }
    void shrink_to_fit() { words_.shrink_to_fit(); }
    size_t size() const { return count_; }
    size_t bytes() const { return words_.size() * sizeof(uint32_t); }
    iterator begin() const { return {words_.data()}; }
    iterator end() const { return {words_.data() + words_.size()}; }

private:
    std::vector<uint32_t> words_;
    size_t count_ = 0;
};

struct Tick {
    double ts;               // epoch seconds
    Rows rows;
    SysValues sys;
};

struct Peak {
    float v = 0;
    double ts = 0;           // 0: no peak yet
};
using Peaks = std::array<Peak, kProcMetrics>;

// Entity key: a PID, or a name id with bit 32 set
constexpr uint64_t pid_key(uint32_t pid) { return pid; }
constexpr uint64_t name_key(uint32_t name) { return 1ull << 32 | name; }
constexpr bool is_group(uint64_t key) { return key >> 32; }

// One row of the ranking table
struct EntityRow {
    uint64_t key;
    uint32_t name;
    std::vector<uint32_t> pids;     // sorted
    bool alive;                     // present in the last tick
    Values cur, avg, max;           // last tick (CPU: mean over the smoothing span), window mean, window max
    Peaks peaks;
};

enum class Field { Current, Average, Maximum, Peak };

struct Latest {
    double ts;
    SysValues sys;
    size_t procs;
};

class History {
public:
    explicit History(size_t maxlen) : maxlen_(maxlen < 2 ? 2 : maxlen) {}

    uint32_t intern(std::string_view name);
    std::string name(uint32_t id) const;
    void append(Tick&& tick);
    void set_maxlen(size_t maxlen);
    void clear();

    size_t size() const;
    double span() const;                           // seconds from the first to the last tick
    std::optional<Latest> latest() const;
    Peaks peaks(uint64_t key) const;
    Peak system_peak(int metric) const;

    // One row per entity over the last `seconds`, unsorted; rank() orders and cuts it.
    // The current CPU value is the mean over the last `cpu_smooth` seconds, so the
    // column and a sort on it do not jump with every sample.
    std::vector<EntityRow> aggregate(double seconds, bool grouped, bool hide_idle, std::string_view filter,
                                     double cpu_smooth, double now) const;

    // Per-tick values over the last `seconds`. out[i] follows metrics[i] (or entities[i]).
    std::vector<double> system_series(const Metric* metrics, int count, double seconds, double now,
                                      std::vector<double>* out) const;
    // A group entity sums every process of that name plus the given PIDs.
    std::vector<double> entity_series(uint64_t key, const std::vector<uint32_t>& pids, const Metric* metrics,
                                      int count, double seconds, double now, std::vector<double>* out) const;
    std::vector<double> multi_series(const std::vector<const EntityRow*>& entities, Metric metric, double seconds,
                                     double now, std::vector<std::vector<double>>& out) const;

private:
    size_t first_in_window(double seconds, double now) const;     // caller holds mu_
    void update_peaks(const Tick& tick);

    mutable std::mutex mu_;
    size_t maxlen_;
    std::deque<Tick> ticks_;
    std::vector<std::string> names_;
    std::unordered_map<std::string, uint32_t> name_ids_;
    std::unordered_map<uint64_t, Peaks> peaks_;
    std::array<Peak, kSysMetrics> sys_peaks_{};
    std::unordered_map<uint32_t, double> last_seen_;
    uint64_t appended_ = 0;
};

std::vector<EntityRow> rank(const std::vector<EntityRow>& rows, Metric metric, Field field, bool descending,
                            size_t limit, bool only_alive = false);
