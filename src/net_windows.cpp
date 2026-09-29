// Windows capture: an ETW real-time session on Microsoft-Windows-Kernel-Network.
//
// Its send/receive events carry the owning PID and the byte count, so there is no packet
// driver and no socket table. Starting a session needs administrator rights.
#include "net.h"

#include <windows.h>
#include <evntcons.h>
#include <evntrace.h>

#include <cstring>
#include <thread>
#include <vector>

#include "log.h"
#include "sys.h"

namespace {

// {7DD42A49-5329-4832-8DFD-43D979153A88}
constexpr GUID kProvider = {0x7dd42a49, 0x5329, 0x4832, {0x8d, 0xfd, 0x43, 0xd9, 0x79, 0x15, 0x3a, 0x88}};
constexpr wchar_t kSession[] = L"Mimir-KernelNetwork";
constexpr ULONGLONG kIpv4 = 0x10, kIpv6 = 0x20;              // provider keywords

// EVENT_TRACE_PROPERTIES followed by room for the session name
std::vector<unsigned char> properties() {
    std::vector<unsigned char> buf(sizeof(EVENT_TRACE_PROPERTIES) + sizeof kSession);
    auto* p = reinterpret_cast<EVENT_TRACE_PROPERTIES*>(buf.data());
    p->Wnode.BufferSize = ULONG(buf.size());
    p->Wnode.Flags = WNODE_FLAG_TRACED_GUID;
    p->Wnode.ClientContext = 1;                                // QueryPerformanceCounter time stamps
    p->LogFileMode = EVENT_TRACE_REAL_TIME_MODE;
    p->FlushTimer = 1;                                         // deliver buffered events every second
    p->LoggerNameOffset = sizeof(EVENT_TRACE_PROPERTIES);
    return buf;
}

class Etw : public Capture {
public:
    Etw() {
        if (!can_capture()) reason_ = "needs administrator rights";
    }
    ~Etw() override { stop(); }

    void start() override {
        if (!available() || active_) return;
        if (thread_.joinable()) thread_.join();
        auto props = properties();
        auto* p = reinterpret_cast<EVENT_TRACE_PROPERTIES*>(props.data());
        ControlTraceW(0, kSession, p, EVENT_TRACE_CONTROL_STOP);   // a session left behind by a crash
        props = properties();
        p = reinterpret_cast<EVENT_TRACE_PROPERTIES*>(props.data());
        ULONG rc = StartTraceW(&session_, kSession, p);
        if (rc == ERROR_SUCCESS)
            rc = EnableTraceEx2(session_, &kProvider, EVENT_CONTROL_CODE_ENABLE_PROVIDER, TRACE_LEVEL_INFORMATION,
                                kIpv4 | kIpv6, 0, 0, nullptr);
        EVENT_TRACE_LOGFILEW log{};
        log.LoggerName = const_cast<wchar_t*>(kSession);
        log.ProcessTraceMode = PROCESS_TRACE_MODE_REAL_TIME | PROCESS_TRACE_MODE_EVENT_RECORD;
        log.EventRecordCallback = on_event;
        log.Context = this;
        if (rc == ERROR_SUCCESS && (trace_ = OpenTraceW(&log)) == INVALID_PROCESSTRACE_HANDLE) rc = GetLastError();
        if (rc != ERROR_SUCCESS) {
            fail("ETW session failed, error " + std::to_string(rc));
            log_error("network capture: ETW error %lu", rc);
            stop();
            return;
        }
        active_ = true;
        thread_ = std::thread([this] {
            ProcessTrace(&trace_, 1, nullptr, nullptr);          // returns when the session stops
            active_ = false;
        });
        log_info("network capture started (ETW Kernel-Network)");
    }

    void stop() override {
        if (session_) {
            auto props = properties();
            ControlTraceW(session_, nullptr, reinterpret_cast<EVENT_TRACE_PROPERTIES*>(props.data()),
                          EVENT_TRACE_CONTROL_STOP);
            session_ = 0;
        }
        if (trace_ != INVALID_PROCESSTRACE_HANDLE) {
            CloseTrace(trace_);
            trace_ = INVALID_PROCESSTRACE_HANDLE;
        }
        if (thread_.joinable()) thread_.join();
        active_ = false;
    }

private:
    // Payload: PID (4 bytes), size (4), then the destination address (4 or 16) and more
    static void WINAPI on_event(EVENT_RECORD* e) {
        auto* self = static_cast<Etw*>(e->UserContext);
        ++self->seen_;
        bool out, v6;
        switch (e->EventHeader.EventDescriptor.Id) {
        case 10: out = true, v6 = false; break;         // TCPv4 sent
        case 11: out = false, v6 = false; break;        // TCPv4 received
        case 26: out = true, v6 = true; break;          // TCPv6
        case 27: out = false, v6 = true; break;
        case 42: out = true, v6 = false; break;         // UDPv4
        case 43: out = false, v6 = false; break;
        case 58: out = true, v6 = true; break;          // UDPv6
        case 59: out = false, v6 = true; break;
        default: return;
        }
        const auto* d = static_cast<const unsigned char*>(e->UserData);
        if (e->UserDataLength < (v6 ? 24 : 12)) return;
        static const unsigned char loopback6[16] = {0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1};
        if (v6 ? std::memcmp(d + 8, loopback6, 16) == 0 : d[8] == 127) return;   // local, as on Linux
        uint32_t pid, size;
        std::memcpy(&pid, d, 4);
        std::memcpy(&size, d + 4, 4);
        if (pid) self->count(pid, size, out);
    }

    TRACEHANDLE session_ = 0, trace_ = INVALID_PROCESSTRACE_HANDLE;
    std::thread thread_;
};

}  // namespace

std::unique_ptr<Capture> Capture::create() { return std::make_unique<Etw>(); }
