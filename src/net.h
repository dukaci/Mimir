// Per-process network attribution. Both backends only observe, never sit in the traffic path:
// - Linux: an AF_PACKET socket receives copies of packets; the kernel drops copies, never
//   traffic, when we fall behind. Packets map to PIDs through the socket table.
// - Windows: the Microsoft-Windows-Kernel-Network ETW provider reports bytes per PID
//   directly, the data Resource Monitor shows.
#pragma once

#include <array>
#include <atomic>
#include <cstdint>
#include <memory>
#include <mutex>
#include <string>
#include <unordered_map>

struct NetRate {
    float down = 0, up = 0;             // bytes per second
};

class Capture {
public:
    static std::unique_ptr<Capture> create();
    virtual ~Capture() = default;

    bool available() const { return reason_.empty(); }
    const std::string& reason() const { return reason_; }      // why it is unavailable
    bool active() const { return active_; }
    std::string error() const {
        std::lock_guard lock(mu_);
        return error_;
    }
    uint64_t seen() const { return seen_; }                     // packets (Linux) or events (Windows)
    uint64_t attributed() const { return attributed_; }

    virtual void start() = 0;
    virtual void stop() = 0;

    // Rates since the previous call, then the counters restart
    void take(double now, std::unordered_map<uint32_t, NetRate>& out) {
        out.clear();
        std::lock_guard lock(mu_);
        double dt = now - since_;
        if (since_ > 0 && dt > 0)
            for (auto& [pid, c] : counters_) out[pid] = {float(c[0] / dt), float(c[1] / dt)};
        counters_.clear();
        since_ = now;
    }

protected:
    void count(uint32_t pid, uint32_t bytes, bool outbound) {
        ++attributed_;
        std::lock_guard lock(mu_);
        counters_[pid][outbound] += bytes;
    }
    void fail(const std::string& e) {
        std::lock_guard lock(mu_);
        error_ = e;
    }

    std::string reason_;
    std::atomic<bool> active_{false};
    std::atomic<uint64_t> seen_{0}, attributed_{0};

private:
    mutable std::mutex mu_;
    std::unordered_map<uint32_t, std::array<uint64_t, 2>> counters_;    // pid -> received, sent
    double since_ = 0;
    std::string error_;
};
