#include "gpu.h"

#include <algorithm>
#include <vector>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <pdh.h>
#include <pdhmsg.h>

#include <map>

#include "log.h"
#else
#include <dlfcn.h>
#endif

namespace {

// The few NVML declarations used here (nvml.h, v2 structs used by the _v3 calls)
using nvmlDevice_t = void*;
struct nvmlUtilization_t { unsigned gpu, memory; };
struct nvmlMemory_t { unsigned long long total, free, used; };
struct nvmlProcessUtilizationSample_t { unsigned pid; unsigned long long timeStamp; unsigned smUtil, memUtil, encUtil, decUtil; };
struct nvmlProcessInfo_t { unsigned pid; unsigned long long usedGpuMemory; unsigned gpuInstanceId, computeInstanceId; };
constexpr unsigned long long kNotAvailable = ~0ull;      // usedGpuMemory on Windows (WDDM hides it)

struct Nvml {
    void* lib = nullptr;
    int (*Init)() = nullptr;
    int (*Shutdown)() = nullptr;
    int (*GetCount)(unsigned*) = nullptr;
    int (*GetHandle)(unsigned, nvmlDevice_t*) = nullptr;
    int (*GetName)(nvmlDevice_t, char*, unsigned) = nullptr;
    int (*GetUtil)(nvmlDevice_t, nvmlUtilization_t*) = nullptr;
    int (*GetMem)(nvmlDevice_t, nvmlMemory_t*) = nullptr;
    int (*GetProcUtil)(nvmlDevice_t, nvmlProcessUtilizationSample_t*, unsigned*, unsigned long long) = nullptr;
    int (*GetGraphics)(nvmlDevice_t, unsigned*, nvmlProcessInfo_t*) = nullptr;
    int (*GetCompute)(nvmlDevice_t, unsigned*, nvmlProcessInfo_t*) = nullptr;

    void* symbol(const char* name) {
#ifdef _WIN32
        return reinterpret_cast<void*>(GetProcAddress(HMODULE(lib), name));
#else
        return dlsym(lib, name);
#endif
    }
    template <class F> bool bind(F& f, const char* name) { return (f = reinterpret_cast<F>(symbol(name))) != nullptr; }

    bool load() {
#ifdef _WIN32
        lib = LoadLibraryW(L"nvml.dll");
        if (!lib) lib = LoadLibraryW(L"C:\\Program Files\\NVIDIA Corporation\\NVSMI\\nvml.dll");
#else
        lib = dlopen("libnvidia-ml.so.1", RTLD_NOW);
#endif
        return lib && bind(Init, "nvmlInit_v2") && bind(Shutdown, "nvmlShutdown") &&
               bind(GetCount, "nvmlDeviceGetCount_v2") && bind(GetHandle, "nvmlDeviceGetHandleByIndex_v2") &&
               bind(GetName, "nvmlDeviceGetName") && bind(GetUtil, "nvmlDeviceGetUtilizationRates") &&
               bind(GetMem, "nvmlDeviceGetMemoryInfo") && bind(GetProcUtil, "nvmlDeviceGetProcessUtilization") &&
               bind(GetGraphics, "nvmlDeviceGetGraphicsRunningProcesses_v3") &&
               bind(GetCompute, "nvmlDeviceGetComputeRunningProcesses_v3");
    }
};

}  // namespace

struct Gpu::State {
    Nvml nvml;
    nvmlDevice_t dev = nullptr;
    bool nvml_ok = false;
    unsigned long long last_util_ts = 0;
    std::vector<nvmlProcessUtilizationSample_t> util_buf;
    std::vector<nvmlProcessInfo_t> proc_buf;
    std::string name, reason;
#ifdef _WIN32
    PDH_HQUERY query = nullptr;
    PDH_HCOUNTER h_util = nullptr, h_mem = nullptr, h_adapter = nullptr;
    bool pdh_ok = false;
    std::vector<BYTE> buf;
#endif

    void open_nvml() {
        if (!nvml.load()) {
            reason = "NVIDIA driver (NVML) not found";
            return;
        }
        unsigned count = 0;
        char gpu_name[96] = {};
        if (nvml.Init() != 0 || nvml.GetCount(&count) != 0 || count < 1 || nvml.GetHandle(0, &dev) != 0) {
            reason = "no NVIDIA GPU";
            return;
        }
        nvml.GetName(dev, gpu_name, sizeof gpu_name);
        name = gpu_name;
        nvml_ok = true;
    }

    // One call into a reused buffer; a second only when the buffer was too small
    template <class T, class F> unsigned fetch(std::vector<T>& out, F&& call) {
        if (out.empty()) out.resize(64);
        unsigned n = unsigned(out.size());
        int rc = call(out.data(), &n);
        if (rc == 7 /* INSUFFICIENT_SIZE */) {
            out.resize(n + 16);
            n = unsigned(out.size());
            rc = call(out.data(), &n);
        }
        return rc == 0 ? n : 0;
    }

    void nvml_processes(std::unordered_map<uint32_t, ProcGpu>& procs) {
        unsigned n = fetch(util_buf, [&](nvmlProcessUtilizationSample_t* b, unsigned* c) {
            return nvml.GetProcUtil(dev, b, c, last_util_ts);
        });
        for (unsigned i = 0; i < n; ++i) {
            auto& s = util_buf[i];
            last_util_ts = std::max(last_util_ts, s.timeStamp);
            float u = std::min(100.0f, float(s.smUtil + s.encUtil + s.decUtil));
            procs[s.pid].util = std::max(procs[s.pid].util, u);
        }
        for (auto get : {nvml.GetGraphics, nvml.GetCompute}) {
            n = fetch(proc_buf, [&](nvmlProcessInfo_t* b, unsigned* c) { return get(dev, c, b); });
            for (unsigned i = 0; i < n; ++i)
                if (proc_buf[i].usedGpuMemory && proc_buf[i].usedGpuMemory != kNotAvailable)
                    procs[proc_buf[i].pid].mem = std::max(procs[proc_buf[i].pid].mem, float(proc_buf[i].usedGpuMemory));
        }
    }

#ifdef _WIN32
    void open_pdh() {
        pdh_ok = PdhOpenQueryW(nullptr, 0, &query) == ERROR_SUCCESS &&
                 PdhAddEnglishCounterW(query, L"\\GPU Engine(*)\\Utilization Percentage", 0, &h_util) == ERROR_SUCCESS &&
                 PdhAddEnglishCounterW(query, L"\\GPU Process Memory(*)\\Dedicated Usage", 0, &h_mem) == ERROR_SUCCESS &&
                 PdhAddEnglishCounterW(query, L"\\GPU Adapter Memory(*)\\Dedicated Usage", 0, &h_adapter) == ERROR_SUCCESS;
        if (pdh_ok)
            PdhCollectQueryData(query);                   // the first collection primes the rate counters
        else
            log_info("GPU performance counters unavailable");
    }

    // f(instance name, value) for every instance of a counter
    template <class F> void each(PDH_HCOUNTER h, F&& f) {
        DWORD size = 0, count = 0;
        if (PdhGetFormattedCounterArrayW(h, PDH_FMT_DOUBLE, &size, &count, nullptr) != PDH_MORE_DATA) return;
        buf.resize(size);
        auto* items = reinterpret_cast<PDH_FMT_COUNTERVALUE_ITEM_W*>(buf.data());
        if (PdhGetFormattedCounterArrayW(h, PDH_FMT_DOUBLE, &size, &count, items) != ERROR_SUCCESS) return;
        for (DWORD i = 0; i < count; ++i) f(items[i].szName, items[i].FmtValue.doubleValue);
    }

    static uint32_t pid_of(const wchar_t* inst) {            // "pid_1234_luid_..._engtype_3D"
        const wchar_t* p = wcsstr(inst, L"pid_");
        return p ? uint32_t(wcstoul(p + 4, nullptr, 10)) : 0;
    }

    void pdh_sample(std::unordered_map<uint32_t, ProcGpu>& procs, float& util, float& adapter_mem) {
        if (PdhCollectQueryData(query) != ERROR_SUCCESS) return;
        std::map<std::wstring, double> engines;              // engine type -> summed use
        each(h_util, [&](const wchar_t* inst, double v) {
            uint32_t pid = pid_of(inst);
            if (!pid) return;
            const wchar_t* e = wcsstr(inst, L"engtype_");
            engines[e ? e + 8 : L"?"] += v;
            procs[pid].util = std::max(procs[pid].util, std::min(100.0f, float(v)));
        });
        each(h_mem, [&](const wchar_t* inst, double v) {
            if (uint32_t pid = pid_of(inst)) procs[pid].mem += float(v);
        });
        util = 0;
        for (auto& [type, v] : engines) util = std::max(util, std::min(100.0f, float(v)));
        adapter_mem = 0;
        each(h_adapter, [&](const wchar_t*, double v) { adapter_mem += float(v); });
    }
#endif
};

Gpu::Gpu() : s_(std::make_unique<State>()) {
    s_->open_nvml();
#ifdef _WIN32
    s_->open_pdh();
    if (s_->name.empty() && s_->pdh_ok) s_->name = "GPU (performance counters)";
#endif
}

Gpu::~Gpu() {
    if (s_->nvml_ok) s_->nvml.Shutdown();
#ifdef _WIN32
    if (s_->query) PdhCloseQuery(s_->query);
#endif
}

bool Gpu::available() const { return s_->nvml_ok || per_process(); }

bool Gpu::per_process() const {
#ifdef _WIN32
    return s_->pdh_ok;
#else
    return s_->nvml_ok;
#endif
}

const std::string& Gpu::name() const { return s_->name; }
const std::string& Gpu::reason() const { return s_->reason; }

const char* Gpu::source() const {
#ifdef _WIN32
    if (s_->pdh_ok) return "counters";
#endif
    return s_->nvml_ok ? "nvml" : "none";
}

void Gpu::sample(std::unordered_map<uint32_t, ProcGpu>& procs, GpuTotals& totals) {
    State& s = *s_;
    procs.clear();
    totals = {};
#ifdef _WIN32
    if (s.pdh_ok) s.pdh_sample(procs, totals.util, totals.mem_used);
#else
    if (s.nvml_ok) s.nvml_processes(procs);
#endif
    nvmlUtilization_t u;
    nvmlMemory_t m;
    if (s.nvml_ok && s.nvml.GetUtil(s.dev, &u) == 0 && s.nvml.GetMem(s.dev, &m) == 0)
        totals = {float(u.gpu), float(m.used)};              // NVML is exact for the whole GPU
}
