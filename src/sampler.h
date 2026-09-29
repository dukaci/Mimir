// Background thread: process, GPU and capture data into one History tick per interval.
#pragma once

#include <atomic>
#include <condition_variable>
#include <functional>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>

#include "gpu.h"
#include "history.h"
#include "net.h"
#include "sys.h"

class Sampler {
public:
    Sampler(History& history, Capture& net, Gpu& gpu, std::function<void()> on_tick);
    ~Sampler() { stop(); }
    void start();
    void stop();

    // Written by the UI thread
    std::atomic<double> interval{1.0};        // seconds
    std::atomic<double> cpu_threshold{0.0};   // % of one core; quieter processes are not stored
    std::atomic<bool> paused{false};

    // Read by the UI thread
    std::atomic<double> last_ms{0};
    std::atomic<uint64_t> mem_total{0};
    std::string last_error() const {
        std::lock_guard lock(mu_);
        return error_;
    }

private:
    void loop();
    void sample();

    History& history_;
    Capture& net_;
    Gpu& gpu_;
    std::function<void()> on_tick_;
    ProcessReader procs_;
    int cpus_ = cpu_count();

    struct Prev {
        double cpu_time;
        uint64_t read, write;
    };
    std::unordered_map<uint32_t, Prev> prev_;
    std::unordered_map<uint32_t, std::pair<std::string, uint32_t>> ids_;    // pid -> name, History name id
    double prev_ts_ = 0;
    SystemCounters prev_sys_{};
    std::unordered_map<uint32_t, ProcGpu> gpu_procs_;
    std::unordered_map<uint32_t, NetRate> net_rates_;

    std::thread thread_;
    mutable std::mutex mu_;
    std::condition_variable wake_;
    bool running_ = false;
    std::string error_;
};
