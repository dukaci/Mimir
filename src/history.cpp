#include "history.h"

#include <algorithm>
#include <cctype>

namespace {

std::string lower(std::string_view s) {
    std::string out(s);
    for (char& c : out) c = char(std::tolower(static_cast<unsigned char>(c)));
    return out;
}

void bump(Peaks& peaks, const float* v, double ts) {
    for (int i = 0; i < kProcMetrics; ++i)
        if (peaks[i].ts == 0 || v[i] > peaks[i].v) peaks[i] = {v[i], ts};
}

}  // namespace

uint32_t History::intern(std::string_view name) {
    std::lock_guard lock(mu_);
    auto it = name_ids_.find(std::string(name));
    if (it != name_ids_.end()) return it->second;
    uint32_t id = uint32_t(names_.size());
    names_.emplace_back(name);
    name_ids_.emplace(names_.back(), id);
    return id;
}

std::string History::name(uint32_t id) const {
    std::lock_guard lock(mu_);
    return id < names_.size() ? names_[id] : std::string();
}

void History::append(Tick&& tick) {
    std::lock_guard lock(mu_);
    update_peaks(tick);
    tick.rows.shrink_to_fit();
    ticks_.push_back(std::move(tick));
    while (ticks_.size() > maxlen_) ticks_.pop_front();
    if (++appended_ % 120 == 0) {       // forget the peaks of PIDs gone for long
        double horizon = ticks_.back().ts - 20.0 * double(maxlen_);
        for (auto it = last_seen_.begin(); it != last_seen_.end();) {
            if (it->second < horizon) {
                peaks_.erase(pid_key(it->first));
                it = last_seen_.erase(it);
            } else {
                ++it;
            }
        }
    }
}

void History::update_peaks(const Tick& tick) {
    std::unordered_map<uint32_t, Values> by_name;        // a group's peak is the peak of its per-tick sum
    for (const Row& r : tick.rows) {
        last_seen_[r.pid] = tick.ts;
        bump(peaks_[pid_key(r.pid)], r.v, tick.ts);
        auto [it, fresh] = by_name.try_emplace(r.name);
        for (int i = 0; i < kProcMetrics; ++i) it->second[i] = fresh ? r.v[i] : it->second[i] + r.v[i];
    }
    for (auto& [name, v] : by_name) bump(peaks_[name_key(name)], v.data(), tick.ts);
    for (int i = 0; i < kSysMetrics; ++i)
        if (sys_peaks_[i].ts == 0 || tick.sys[i] > sys_peaks_[i].v) sys_peaks_[i] = {tick.sys[i], tick.ts};
}

void History::set_maxlen(size_t maxlen) {
    std::lock_guard lock(mu_);
    maxlen_ = maxlen < 2 ? 2 : maxlen;
    while (ticks_.size() > maxlen_) ticks_.pop_front();
}

void History::clear() {
    std::lock_guard lock(mu_);
    ticks_.clear();
    peaks_.clear();
    last_seen_.clear();
    sys_peaks_ = {};
}

size_t History::size() const {
    std::lock_guard lock(mu_);
    return ticks_.size();
}

double History::span() const {
    std::lock_guard lock(mu_);
    return ticks_.size() < 2 ? 0.0 : ticks_.back().ts - ticks_.front().ts;
}

std::optional<Latest> History::latest() const {
    std::lock_guard lock(mu_);
    if (ticks_.empty()) return std::nullopt;
    const Tick& t = ticks_.back();
    return Latest{t.ts, t.sys, t.rows.size()};
}

Peaks History::peaks(uint64_t key) const {
    std::lock_guard lock(mu_);
    auto it = peaks_.find(key);
    return it == peaks_.end() ? Peaks{} : it->second;
}

Peak History::system_peak(int metric) const {
    std::lock_guard lock(mu_);
    return sys_peaks_[metric];
}

size_t History::first_in_window(double seconds, double now) const {
    double cutoff = now - seconds;
    size_t i = ticks_.size();
    while (i > 0 && ticks_[i - 1].ts >= cutoff) --i;
    return i;
}

std::vector<EntityRow> History::aggregate(double seconds, bool grouped, bool hide_idle, std::string_view filter,
                                          double cpu_smooth, double now) const {
    std::lock_guard lock(mu_);
    size_t b = first_in_window(seconds, now), e = ticks_.size();
    if (b == e) return {};
    double last_ts = ticks_[e - 1].ts;
    size_t recent = e - 1;                             // first tick of the CPU smoothing span
    while (recent > b && ticks_[recent - 1].ts > last_ts - cpu_smooth) --recent;
    std::string needle = lower(filter);

    std::vector<int8_t> excluded(names_.size(), -1);   // per name id, decided once
    auto is_excluded = [&](uint32_t id) {
        if (excluded[id] < 0) {
            std::string l = lower(names_[id]);
            excluded[id] = (hide_idle && (l == "system idle process" || l == "idle")) ||
                           (!needle.empty() && l.find(needle) == std::string::npos);
        }
        return excluded[id] != 0;
    };

    struct Acc {
        EntityRow row;
        Values sum{};
        float cpu_recent = 0;
    };
    std::vector<Acc> acc;                              // first-seen order, so ties keep a stable order
    std::unordered_map<uint64_t, size_t> index;
    struct Part {
        uint64_t key;
        uint32_t name;
        Values v;
    };
    std::vector<Part> tick_parts;                      // this tick, summed per entity
    std::unordered_map<uint64_t, size_t> tick_index;

    for (size_t t = b; t < e; ++t) {
        tick_parts.clear();
        tick_index.clear();
        for (const Row& r : ticks_[t].rows) {
            if (is_excluded(r.name)) continue;
            uint64_t key = grouped ? name_key(r.name) : pid_key(r.pid);
            auto [it, fresh] = tick_index.try_emplace(key, tick_parts.size());
            if (fresh) {
                tick_parts.push_back({key, r.name, {}});
                std::copy(r.v, r.v + kProcMetrics, tick_parts.back().v.begin());
            } else {
                Values& v = tick_parts[it->second].v;
                for (int i = 0; i < kProcMetrics; ++i) v[i] += r.v[i];
            }
            auto [ai, afresh] = index.try_emplace(key, acc.size());
            if (afresh) acc.push_back({EntityRow{key, r.name, {}, false, {}, {}, {}, {}}});
            acc[ai->second].row.pids.push_back(r.pid);
        }
        for (const Part& p : tick_parts) {
            Acc& a = acc[index[p.key]];
            for (int i = 0; i < kProcMetrics; ++i) {
                a.sum[i] += p.v[i];
                a.row.max[i] = std::max(a.row.max[i], p.v[i]);
            }
            if (t >= recent) a.cpu_recent += p.v[CPU];
            if (t == e - 1) {
                a.row.cur = p.v;
                a.row.alive = true;
            }
        }
    }

    float n = float(e - b), n_recent = float(e - recent);
    std::vector<EntityRow> rows;
    rows.reserve(acc.size());
    for (Acc& a : acc) {
        EntityRow& r = a.row;
        r.cur[CPU] = a.cpu_recent / n_recent;
        for (int i = 0; i < kProcMetrics; ++i) r.avg[i] = a.sum[i] / n;
        std::sort(r.pids.begin(), r.pids.end());
        r.pids.erase(std::unique(r.pids.begin(), r.pids.end()), r.pids.end());
        auto it = peaks_.find(r.key);
        if (it != peaks_.end()) r.peaks = it->second;
        rows.push_back(std::move(r));
    }
    return rows;
}

std::vector<EntityRow> rank(const std::vector<EntityRow>& rows, Metric metric, Field field, bool descending,
                            size_t limit, bool only_alive) {
    auto value = [&](const EntityRow& r) {
        switch (field) {
        case Field::Peak: return r.peaks[metric].v;
        case Field::Average: return r.avg[metric];
        case Field::Maximum: return r.max[metric];
        default: return r.cur[metric];
        }
    };
    std::vector<const EntityRow*> order;
    for (const EntityRow& r : rows)
        if (!only_alive || r.alive) order.push_back(&r);
    std::stable_sort(order.begin(), order.end(), [&](const EntityRow* a, const EntityRow* b) {
        return descending ? value(*a) > value(*b) : value(*a) < value(*b);
    });
    std::vector<EntityRow> out;
    for (size_t i = 0; i < order.size() && i < limit; ++i) out.push_back(*order[i]);
    return out;
}

std::vector<double> History::system_series(const Metric* metrics, int count, double seconds, double now,
                                           std::vector<double>* out) const {
    std::lock_guard lock(mu_);
    std::vector<double> ts;
    for (int j = 0; j < count; ++j) out[j].clear();
    for (size_t t = first_in_window(seconds, now); t < ticks_.size(); ++t) {
        ts.push_back(ticks_[t].ts);
        for (int j = 0; j < count; ++j) out[j].push_back(ticks_[t].sys[metrics[j]]);
    }
    return ts;
}

std::vector<double> History::entity_series(uint64_t key, const std::vector<uint32_t>& pids, const Metric* metrics,
                                           int count, double seconds, double now, std::vector<double>* out) const {
    std::lock_guard lock(mu_);
    std::vector<double> ts;
    for (int j = 0; j < count; ++j) out[j].clear();
    uint32_t id = uint32_t(key);
    for (size_t t = first_in_window(seconds, now); t < ticks_.size(); ++t) {
        ts.push_back(ticks_[t].ts);
        double sum[kProcMetrics] = {};
        for (const Row& r : ticks_[t].rows) {
            bool match = is_group(key) ? r.name == id || std::binary_search(pids.begin(), pids.end(), r.pid)
                                       : r.pid == id;
            if (match)
                for (int j = 0; j < count; ++j) sum[j] += r.v[metrics[j]];
        }
        for (int j = 0; j < count; ++j) out[j].push_back(sum[j]);
    }
    return ts;
}

std::vector<double> History::multi_series(const std::vector<const EntityRow*>& entities, Metric metric,
                                          double seconds, double now, std::vector<std::vector<double>>& out) const {
    std::unordered_map<uint32_t, size_t> by_pid, by_name;
    for (size_t i = 0; i < entities.size(); ++i) {
        const EntityRow& e = *entities[i];
        if (is_group(e.key))
            by_name[uint32_t(e.key)] = i;
        else
            for (uint32_t pid : e.pids) by_pid[pid] = i;
    }
    std::lock_guard lock(mu_);
    size_t b = first_in_window(seconds, now), n = ticks_.size() - b;
    std::vector<double> ts(n);
    out.assign(entities.size(), std::vector<double>(n, 0.0));
    for (size_t t = 0; t < n; ++t) {
        const Tick& tick = ticks_[b + t];
        ts[t] = tick.ts;
        for (const Row& r : tick.rows) {
            if (auto it = by_pid.find(r.pid); it != by_pid.end())
                out[it->second][t] += r.v[metric];
            else if (auto jt = by_name.find(r.name); jt != by_name.end())
                out[jt->second][t] += r.v[metric];
        }
    }
    return ts;
}
