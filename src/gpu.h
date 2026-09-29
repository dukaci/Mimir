// GPU statistics from two sources:
// - NVML (NVIDIA; loaded at run time, so no SDK): whole-GPU use, VRAM and name,
//   plus per-process figures on Linux.
// - Windows performance counters "GPU Engine" / "GPU Process Memory" (the data
//   behind Task Manager's GPU columns): per-process use and VRAM, any vendor, no admin.
#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <unordered_map>

struct ProcGpu {
    float util = 0;          // busiest engine, percent
    float mem = 0;           // dedicated VRAM, bytes
};

struct GpuTotals {
    float util = 0;
    float mem_used = 0;
};

class Gpu {
public:
    Gpu();
    ~Gpu();
    bool available() const;
    bool per_process() const;
    const std::string& name() const;
    const std::string& reason() const;       // why it is unavailable
    const char* source() const;              // "counters", "nvml" or "none"

    // One call per tick
    void sample(std::unordered_map<uint32_t, ProcGpu>& procs, GpuTotals& totals);

private:
    struct State;
    std::unique_ptr<State> s_;
};
