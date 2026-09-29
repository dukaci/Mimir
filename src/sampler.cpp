#include "sampler.h"

#include <algorithm>
#include <chrono>

#include "log.h"

Sampler::Sampler(History& history, Capture& net, Gpu& gpu, std::function<void()> on_tick)
    : history_(history), net_(net), gpu_(gpu), on_tick_(std::move(on_tick)) {}

void Sampler::start() {
    std::lock_guard lock(mu_);
    if (running_) return;
    running_ = true;
    thread_ = std::thread([this] { loop(); });
}

void Sampler::stop() {
    {
        std::lock_guard lock(mu_);
        running_ = false;
    }
    wake_.notify_all();
    if (thread_.joinable()) thread_.join();
}

void Sampler::loop() {
    using clock = std::chrono::steady_clock;
    auto next = clock::now();
    std::unique_lock lock(mu_);
    while (running_) {
        lock.unlock();
        if (!paused) {
            auto t0 = clock::now();
            try {
                sample();
                std::lock_guard l(mu_);
                error_.clear();
            } catch (const std::exception& e) {              // keep sampling; the UI shows the error
                std::lock_guard l(mu_);
                error_ = e.what();
                log_error("sampling failed: %s", e.what());
            }
            last_ms = std::chrono::duration<double, std::milli>(clock::now() - t0).count();
            if (on_tick_) on_tick_();
        }
        // a fixed cadence: the next tick is due one interval after the previous one was
        next += std::chrono::duration_cast<clock::duration>(std::chrono::duration<double>(interval.load()));
        if (next < clock::now()) next = clock::now();
        lock.lock();
        wake_.wait_until(lock, next, [this] { return !running_; });
    }
}

void Sampler::sample() {
    double now = wall_now();
    if (net_.active())
        net_.take(now, net_rates_);
    else
        net_rates_.clear();
    GpuTotals gpu;
    gpu_.sample(gpu_procs_, gpu);
    double dt = prev_ts_ > 0 ? now - prev_ts_ : 0;
    double threshold = cpu_threshold;

    Tick tick{now, {}, {}};
    const auto& procs = procs_.read();
    std::unordered_map<uint32_t, Prev> prev;
    prev.reserve(procs.size());
    std::unordered_map<uint32_t, std::pair<std::string, uint32_t>> ids;
    ids.reserve(procs.size());
    for (const ProcInfo& p : procs) {
        Row r{p.pid, 0, {}};
        auto it = prev_.find(p.pid);
        double core = 0;                                 // % of one core
        if (it != prev_.end() && dt > 0) {
            core = std::max(0.0, p.cpu_time - it->second.cpu_time) / dt * 100.0;
            r.v[DISK_R] = float(double(p.read_bytes - std::min(p.read_bytes, it->second.read)) / dt);
            r.v[DISK_W] = float(double(p.write_bytes - std::min(p.write_bytes, it->second.write)) / dt);
        }
        prev.emplace(p.pid, Prev{p.cpu_time, p.read_bytes, p.write_bytes});
        r.v[CPU] = float(core / cpus_);
        r.v[MEM] = float(p.mem);
        if (auto n = net_rates_.find(p.pid); n != net_rates_.end()) r.v[NET_D] = n->second.down, r.v[NET_U] = n->second.up;
        if (auto g = gpu_procs_.find(p.pid); g != gpu_procs_.end()) r.v[GPU] = g->second.util, r.v[VRAM] = g->second.mem;
        r.v[THREADS] = float(p.threads);
        if (threshold > 0 && core < threshold &&
            !(r.v[DISK_R] || r.v[DISK_W] || r.v[NET_D] || r.v[NET_U] || r.v[GPU]))
            continue;                                    // the user asked to skip quiet processes
        auto id = ids_.find(p.pid);
        uint32_t name = id != ids_.end() && id->second.first == p.name ? id->second.second : history_.intern(p.name);
        ids.emplace(p.pid, std::make_pair(p.name, name));
        r.name = name;
        tick.rows.push_back(r);
    }
    prev_ = std::move(prev);
    ids_ = std::move(ids);

    SystemCounters sys = read_system();
    mem_total = sys.mem_total;
    tick.sys[MEM] = float(sys.mem_used);
    if (dt > 0) {
        auto rate = [&](uint64_t cur, uint64_t old) { return float(double(cur - std::min(cur, old)) / dt); };
        double total = sys.cpu_total - prev_sys_.cpu_total;
        tick.sys[CPU] = total > 0 ? float(std::clamp((sys.cpu_busy - prev_sys_.cpu_busy) / total * 100, 0.0, 100.0)) : 0;
        tick.sys[DISK_R] = rate(sys.disk_read, prev_sys_.disk_read);
        tick.sys[DISK_W] = rate(sys.disk_write, prev_sys_.disk_write);
        tick.sys[NET_D] = rate(sys.net_recv, prev_sys_.net_recv);
        tick.sys[NET_U] = rate(sys.net_sent, prev_sys_.net_sent);
    }
    tick.sys[GPU] = gpu.util;
    tick.sys[VRAM] = gpu.mem_used;
    prev_sys_ = sys;
    prev_ts_ = now;
    history_.append(std::move(tick));
}
