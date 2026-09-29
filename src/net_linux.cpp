// Linux capture: a ring of AF_PACKET copies + the socket table from /proc.
//
// Needs root or CAP_NET_RAW; reading other users' /proc/<pid>/fd also needs
// CAP_DAC_READ_SEARCH and CAP_SYS_PTRACE (build.sh grants all three to bin/mimir).
#include "net.h"

#include <arpa/inet.h>
#include <dirent.h>
#include <linux/filter.h>
#include <linux/if_ether.h>
#include <linux/if_packet.h>
#include <net/if.h>
#include <poll.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <unistd.h>

#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <thread>
#include <unordered_set>
#include <utility>
#include <vector>

#include "log.h"
#include "sys.h"

namespace {

// Every address as 16 bytes; IPv4 as ::ffff:a.b.c.d, which is also how dual-stack sockets list IPv4 peers.
struct Addr {
    uint64_t hi = 0, lo = 0;
    bool operator==(const Addr&) const = default;
    bool any() const { return hi == 0 && (lo == 0 || lo == 0x0000ffff00000000ull); }   // :: or 0.0.0.0
};

Addr v4(const uint8_t* b) {
    Addr a;
    a.lo = 0x0000ffff00000000ull | uint64_t(b[0]) << 24 | uint64_t(b[1]) << 16 | uint64_t(b[2]) << 8 | b[3];
    return a;
}

Addr v6(const uint8_t* b) {
    Addr a;
    for (int i = 0; i < 8; ++i) a.hi = a.hi << 8 | b[i];
    for (int i = 8; i < 16; ++i) a.lo = a.lo << 8 | b[i];
    return a;
}

struct Local {
    Addr ip;
    uint16_t port;
    bool operator==(const Local&) const = default;
};
struct Conn {
    Local local, remote;
    bool operator==(const Conn&) const = default;
};

uint64_t mix(uint64_t h, uint64_t v) {
    h ^= v + 0x9e3779b97f4a7c15ull + (h << 6) + (h >> 2);
    return h;
}
struct Hash {
    size_t operator()(const Local& l) const { return mix(mix(l.ip.hi, l.ip.lo), l.port); }
    size_t operator()(const Conn& c) const { return mix(operator()(c.local), operator()(c.remote)); }
};

// "0100007F:0035" (IPv4) or 32 hex digits (IPv6): 32-bit words in host (little-endian) order
Local parse_endpoint(const char* s, bool ipv6) {
    uint8_t b[16];
    int words = ipv6 ? 4 : 1;
    for (int w = 0; w < words; ++w) {
        char hex[9];
        std::memcpy(hex, s + w * 8, 8);
        hex[8] = 0;
        uint32_t v = uint32_t(std::strtoul(hex, nullptr, 16));
        std::memcpy(b + w * 4, &v, 4);
    }
    return {ipv6 ? v6(b) : v4(b), uint16_t(std::strtoul(s + words * 8 + 1, nullptr, 16))};
}

class AfPacket : public Capture {
public:
    AfPacket() {
        if (!can_capture()) reason_ = "needs root or CAP_NET_RAW (run ./build.sh)";
    }
    ~AfPacket() override { stop(); }

    void start() override {
        if (!available() || active_) return;
        if (thread_.joinable()) thread_.join();
        active_ = true;
        thread_ = std::thread([this] { run(); });
    }

    void stop() override {
        active_ = false;
        if (thread_.joinable()) thread_.join();
    }

private:
    struct Socket {
        Conn conn;
        uint64_t inode;
    };

    // A TPACKET_V3 ring: the kernel copies the first kSnap bytes of each packet into
    // shared blocks and wakes this thread when a block is full or 100 ms old, so there
    // is no system call per packet. When the ring is full, the kernel drops copies, never traffic.
    void run() {
        constexpr unsigned kBlock = 1 << 17, kBlocks = 4, kSnap = 128;   // IP + port headers only
        sock_filter code[] = {                                           // loopback: dropped; the rest: cut to kSnap
            {BPF_LD | BPF_W | BPF_ABS, 0, 0, uint32_t(SKF_AD_OFF + SKF_AD_IFINDEX)},
            {BPF_JMP | BPF_JEQ | BPF_K, 0, 1, if_nametoindex("lo")},
            {BPF_RET | BPF_K, 0, 0, 0},
            {BPF_RET | BPF_K, 0, 0, kSnap},
        };
        sock_fprog filter{4, code};
        int version = TPACKET_V3;
        tpacket_req3 req{};
        req.tp_block_size = kBlock;
        req.tp_block_nr = kBlocks;
        req.tp_frame_size = 1 << 11;                                     // V3 packs frames; the kernel checks these
        req.tp_frame_nr = kBlock / req.tp_frame_size * kBlocks;
        req.tp_retire_blk_tov = 100;                                     // ms
        sockaddr_ll all{};
        all.sll_family = AF_PACKET;
        all.sll_protocol = htons(ETH_P_ALL);
        void* ring = MAP_FAILED;
        int fd = socket(AF_PACKET, SOCK_DGRAM | SOCK_CLOEXEC, 0);       // DGRAM: packets start at IP; none until bind
        if (fd < 0 || setsockopt(fd, SOL_SOCKET, SO_ATTACH_FILTER, &filter, sizeof filter) ||
            setsockopt(fd, SOL_PACKET, PACKET_VERSION, &version, sizeof version) ||
            setsockopt(fd, SOL_PACKET, PACKET_RX_RING, &req, sizeof req) ||
            (ring = mmap(nullptr, kBlock * kBlocks, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0)) == MAP_FAILED ||
            bind(fd, reinterpret_cast<sockaddr*>(&all), sizeof all)) {
            fail(std::string("capture socket: ") + std::strerror(errno));
            if (ring != MAP_FAILED) munmap(ring, kBlock * kBlocks);
            if (fd >= 0) ::close(fd);
            active_ = false;
            return;
        }
        log_info("network capture started (passive AF_PACKET ring)");
        auto next_refresh = std::chrono::steady_clock::now();
        for (unsigned b = 0; active_;) {
            if (std::chrono::steady_clock::now() >= next_refresh) {
                refresh();
                next_refresh = std::chrono::steady_clock::now() + std::chrono::seconds(1);
            }
            auto* block = reinterpret_cast<tpacket_block_desc*>(static_cast<uint8_t*>(ring) + b * kBlock);
            std::atomic_ref status(block->hdr.bh1.block_status);
            if (!(status.load(std::memory_order_acquire) & TP_STATUS_USER)) {
                pollfd p{fd, POLLIN, 0};
                poll(&p, 1, 250);
                continue;
            }
            const uint8_t* h = reinterpret_cast<const uint8_t*>(block) + block->hdr.bh1.offset_to_first_pkt;
            for (uint32_t i = 0; i < block->hdr.bh1.num_pkts; ++i) {
                auto* t = reinterpret_cast<const tpacket3_hdr*>(h);
                auto* ll = reinterpret_cast<const sockaddr_ll*>(h + TPACKET_ALIGN(sizeof(tpacket3_hdr)));
                ++seen_;
                packet(h + t->tp_net, t->tp_snaplen, t->tp_len, ntohs(ll->sll_protocol),
                       ll->sll_pkttype == PACKET_OUTGOING);
                h += t->tp_next_offset;
            }
            status.store(TP_STATUS_KERNEL, std::memory_order_release);   // hand the block back
            b = (b + 1) % kBlocks;
        }
        munmap(ring, kBlock * kBlocks);
        ::close(fd);
        log_info("network capture stopped");
    }

    void packet(const uint8_t* b, unsigned len, uint32_t size, uint16_t proto, bool out) {
        Addr src, dst;
        unsigned l4, off;
        if (proto == ETH_P_IP && len >= 20) {
            if (((b[6] << 8 | b[7]) & 0x1FFF) != 0) return;  // later fragment: no ports
            l4 = b[9], off = (b[0] & 0x0F) * 4u;
            src = v4(b + 12), dst = v4(b + 16);
        } else if (proto == ETH_P_IPV6 && len >= 40) {
            l4 = b[6], off = 40;
            src = v6(b + 8), dst = v6(b + 24);
        } else {
            return;
        }
        if ((l4 != 6 && l4 != 17) || off + 4 > len) return;
        uint16_t sport = uint16_t(b[off] << 8 | b[off + 1]), dport = uint16_t(b[off + 2] << 8 | b[off + 3]);
        Conn c = out ? Conn{{src, sport}, {dst, dport}} : Conn{{dst, dport}, {src, sport}};
        if (auto it = owner_.find(inode_of(c)); it != owner_.end())
            count(it->second, size, out);
        else if (missed_.size() < 256)
            missed_.insert(c);
    }

    // The socket of a packet: its connection, else its local address, else its port (a wildcard bind)
    uint64_t inode_of(const Conn& c) const {
        if (auto it = conn_inode_.find(c); it != conn_inode_.end()) return it->second;
        if (auto it = local_inode_.find(c.local); it != local_inode_.end()) return it->second;
        if (auto it = port_inode_.find(c.local.port); it != port_inode_.end()) return it->second;
        return 0;
    }

    // The socket table from /proc/net (cheap). /proc/*/fd, costly, is scanned only
    // when traffic flows on a socket whose owner is not known.
    void refresh() {
        sockets_.clear();
        char line[512];
        for (const char* file : {"/proc/net/tcp", "/proc/net/udp", "/proc/net/tcp6", "/proc/net/udp6"}) {
            std::FILE* f = std::fopen(file, "r");
            if (!f) continue;
            bool ipv6 = std::strchr(file, '6') != nullptr;
            bool header = true;
            while (std::fgets(line, sizeof line, f)) {
                if (std::exchange(header, false)) continue;
                char local[64], remote[64];
                unsigned long long inode = 0;
                if (std::sscanf(line, "%*s %63s %63s %*s %*s %*s %*s %*s %*s %llu", local, remote, &inode) != 3 ||
                    !inode)
                    continue;                                 // inode 0: TIME_WAIT, no owner
                sockets_.push_back({{parse_endpoint(local, ipv6), parse_endpoint(remote, ipv6)}, inode});
            }
            std::fclose(f);
        }
        conn_inode_.clear();
        local_inode_.clear();
        port_inode_.clear();
        for (const Socket& s : sockets_) {
            if (s.conn.remote.port) conn_inode_[s.conn] = s.inode;
            local_inode_[s.conn.local] = s.inode;
            if (s.conn.local.ip.any()) port_inode_[s.conn.local.port] = s.inode;
        }
        bool scan = false;
        for (const Conn& c : missed_) {
            uint64_t inode = inode_of(c);
            scan |= inode && !owner_.count(inode) && !tried_.count(inode);
        }
        missed_.clear();
        if (scan) scan_fds();
    }

    void scan_fds() {
        owner_.clear();
        DIR* proc = opendir("/proc");
        if (!proc) return;
        char path[64], link[64];
        while (dirent* p = readdir(proc)) {
            if (p->d_name[0] < '1' || p->d_name[0] > '9') continue;
            std::snprintf(path, sizeof path, "/proc/%s/fd", p->d_name);
            DIR* fds = opendir(path);
            if (!fds) continue;
            uint32_t pid = uint32_t(std::atoi(p->d_name));
            while (dirent* f = readdir(fds)) {
                if (f->d_name[0] == '.') continue;
                ssize_t n = readlinkat(dirfd(fds), f->d_name, link, sizeof link - 1);
                if (n > 9 && std::memcmp(link, "socket:[", 8) == 0) {
                    link[n] = 0;
                    owner_.emplace(std::strtoull(link + 8, nullptr, 10), pid);
                }
            }
            closedir(fds);
        }
        closedir(proc);
        tried_.clear();                    // inodes no scan can place (other users' without rights, kernel)
        for (const Socket& s : sockets_)
            if (!owner_.count(s.inode)) tried_.insert(s.inode);
    }

    std::thread thread_;
    std::vector<Socket> sockets_;
    std::unordered_map<uint64_t, uint32_t> owner_;     // socket inode -> pid
    std::unordered_set<uint64_t> tried_;               // inodes the last scan could not place
    std::unordered_set<Conn, Hash> missed_;            // unplaced traffic since the last refresh
    std::unordered_map<Conn, uint64_t, Hash> conn_inode_;
    std::unordered_map<Local, uint64_t, Hash> local_inode_;
    std::unordered_map<uint16_t, uint64_t> port_inode_;
};

}  // namespace

std::unique_ptr<Capture> Capture::create() { return std::make_unique<AfPacket>(); }
