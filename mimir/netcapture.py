"""Per-process network attribution.

psutil cannot say which process sent or received a packet, so this module
counts bytes per PID by *passively sniffing* traffic and matching each packet
to a socket owner from psutil.net_connections().

Safety rule: the attributor must never sit in the packet path.  On Windows the
WinDivert driver is opened with the SNIFF flag, which hands us a copy of each
packet while the original continues untouched.  If Python falls behind, the
driver drops copies, never real traffic.  Packets are never re-injected.

On Linux an AF_PACKET socket does the same job: the kernel hands it a copy
of each packet, and the socket buffer drops copies when Python falls behind.
It needs root or CAP_NET_RAW (setup.sh grants that to the venv python).
"""

from __future__ import annotations

import logging
import socket
import struct
import threading
import time
from dataclasses import dataclass, field

import psutil

from .platform import IS_LINUX, IS_WINDOWS, can_sniff

log = logging.getLogger(__name__)

MAP_MIN = 2.0       # s: soonest socket-table refresh after a packet of a flow with no known owner
MAP_MAX = 10.0      # s: latest refresh; it also forgets the unknown flows, so they can trigger again

try:
    import pydivert
    PYDIVERT_AVAILABLE = True
except ImportError:      # pragma: no cover
    pydivert = None
    PYDIVERT_AVAILABLE = False


@dataclass
class CaptureStats:
    packets: int = 0
    attributed: int = 0
    started_at: float = 0.0
    error: str = ""


class NetworkAttributor:
    """Contract for backends. Values returned by take() are bytes/second."""

    available: bool = False
    reason: str = "not supported on this platform"
    stats: CaptureStats = field(default_factory=CaptureStats)

    @property
    def active(self) -> bool:
        return False

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def take(self, now: float) -> dict[int, tuple[float, float]]:
        """Return {pid: (down_rate, up_rate)} since the previous take() and reset."""
        return {}


class NullAttributor(NetworkAttributor):
    def __init__(self, reason: str):
        self.reason = reason
        self.stats = CaptureStats()


def _plain(ip: str) -> str:
    """Dual-stack sockets report IPv4 peers as ::ffff:a.b.c.d; packets carry a.b.c.d."""
    return ip[7:] if ip.startswith("::ffff:") else ip


class _Sniffer(NetworkAttributor):
    """Shared part of the backends: psutil socket table, per-PID byte counters.

    A backend supplies _packets(), yielding (src, sport, dst, dport, size, outbound)
    until self._active goes False, and _unblock(), which makes that loop notice.
    """

    def __init__(self, available: bool, reason: str):
        self.stats = CaptureStats()
        self.available, self.reason = available, reason
        self._lock = threading.Lock()
        self._counters: dict[int, list] = {}     # pid -> [bytes_recv, bytes_sent, since]
        self._thread: threading.Thread | None = None
        self._active = False
        self._conn: dict[tuple, int] = {}
        self._local: dict[tuple, int] = {}
        self._port: dict[int, int] = {}
        self._missed: set[tuple] = set()           # flows with no owner, each triggers one refresh
        self._stale = False

    @property
    def active(self) -> bool:
        return self._active

    def start(self) -> None:
        if not self.available or self._active:
            return
        if self._thread and self._thread.is_alive():
            return
        self._active = True
        self.stats = CaptureStats(started_at=time.time())
        self._thread = threading.Thread(target=self._run, name="mimir-sniff", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._active = False
        self._unblock()

    def take(self, now: float) -> dict[int, tuple[float, float]]:
        out = {}
        with self._lock:
            for pid, c in self._counters.items():
                elapsed = now - c[2]
                if elapsed > 0:
                    out[pid] = (c[0] / elapsed, c[1] / elapsed)
                c[0] = c[1] = 0
                c[2] = now
        return out

    # ------------------------------------------------------------ internals
    def _refresh_maps(self) -> None:
        conn, local, port = {}, {}, {}
        try:
            conns = psutil.net_connections(kind="inet")
        except Exception as e:
            log.debug("net_connections failed: %s", e)
            return
        for c in conns:
            if not c.pid or not c.laddr:
                continue
            lip, lport = _plain(c.laddr.ip), c.laddr.port
            if c.raddr:
                conn[(lip, lport, _plain(c.raddr.ip), c.raddr.port)] = c.pid
            local[(lip, lport)] = c.pid
            if lip in ("0.0.0.0", "::"):
                port[lport] = c.pid
        self._conn, self._local, self._port = conn, local, port

    def _map_loop(self) -> None:
        """Rescan the socket table (costly: it reads every /proc/*/fd) only when a packet
        needs it, or every MAP_MAX seconds so closed sockets drop out."""
        last = full = float("-inf")
        while self._active:
            now = time.monotonic()
            if now - full >= MAP_MAX:
                self._missed.clear()
                full = now
            elif not (self._stale and now - last >= MAP_MIN):
                time.sleep(0.1)
                continue
            self._stale = False
            self._refresh_maps()
            last = now

    def _run(self) -> None:
        threading.Thread(target=self._map_loop, name="mimir-sockmap", daemon=True).start()
        try:
            log.info("network capture started (passive sniff mode)")
            for packet in self._packets():
                self.stats.packets += 1
                self._account(*packet)
        except Exception as e:
            if self._active:
                self.stats.error = str(e)
                log.error("network capture stopped: %s", e)
        finally:
            self._active = False
            log.info("network capture stopped")

    def _packets(self):
        raise NotImplementedError

    def _unblock(self) -> None:
        pass

    def _account(self, src: str, sport: int, dst: str, dport: int, size: int, outbound: bool) -> None:
        try:
            if outbound:
                local, key = (src, sport), (src, sport, dst, dport)
            else:
                local, key = (dst, dport), (dst, dport, src, sport)
            pid = self._conn.get(key) or self._local.get(local) or self._port.get(local[1])
            if not pid:
                if key not in self._missed:
                    self._missed.add(key)
                    self._stale = True
                return
            self.stats.attributed += 1
            with self._lock:
                c = self._counters.get(pid)
                if c is None:
                    c = self._counters[pid] = [0, 0, time.time()]
                c[1 if outbound else 0] += size
        except Exception:
            pass


class WinDivertSniffer(_Sniffer):
    """Windows backend: WinDivert in SNIFF mode + psutil socket table."""

    def __init__(self):
        if not PYDIVERT_AVAILABLE:
            super().__init__(False, "pydivert is not installed")
        elif not can_sniff():
            super().__init__(False, "needs administrator rights")
        else:
            super().__init__(True, "")
        self._handle = None

    def _packets(self):
        try:
            with pydivert.WinDivert("tcp or udp", flags=pydivert.Flag.SNIFF) as w:
                self._handle = w
                for packet in w:
                    if not self._active:
                        return
                    ip = packet.ipv4 or packet.ipv6
                    l4 = packet.tcp or packet.udp
                    if ip is None or l4 is None:
                        continue
                    yield (str(ip.src_addr), l4.src_port, str(ip.dst_addr), l4.dst_port,
                           len(packet.raw), packet.is_outbound)
        finally:
            self._handle = None

    def _unblock(self) -> None:
        h = self._handle
        if h is not None:
            try:
                h.close()        # unblocks the receive loop
            except Exception:
                pass


class AfPacketSniffer(_Sniffer):
    """Linux backend: AF_PACKET socket (a copy of every packet) + psutil socket table."""

    ETH_P_ALL, ETH_P_IP, ETH_P_IPV6 = 0x0003, 0x0800, 0x86DD
    PACKET_OUTGOING = 4

    def __init__(self):
        if not can_sniff():
            super().__init__(False, "needs root or CAP_NET_RAW (run ./setup.sh)")
        else:
            super().__init__(True, "")

    def _packets(self):
        # SOCK_DGRAM strips the link header, so wifi, ethernet and tun packets all start at IP
        sock = socket.socket(socket.AF_PACKET, socket.SOCK_DGRAM, socket.htons(self.ETH_P_ALL))
        sock.settimeout(0.5)          # lets the loop notice stop()
        buf = bytearray(128)          # IP + port headers only; MSG_TRUNC still reports the full size
        try:
            while self._active:
                try:
                    size, (ifname, proto, pkttype, _, _) = sock.recvfrom_into(buf, 0, socket.MSG_TRUNC)
                except socket.timeout:
                    continue
                if ifname == "lo":
                    continue          # local traffic, and each packet shows up there twice
                if proto == self.ETH_P_IP:
                    if struct.unpack_from("!H", buf, 6)[0] & 0x1FFF:
                        continue      # later fragment, no ports
                    l4, offset = buf[9], (buf[0] & 0x0F) * 4
                    src = socket.inet_ntop(socket.AF_INET, bytes(buf[12:16]))
                    dst = socket.inet_ntop(socket.AF_INET, bytes(buf[16:20]))
                elif proto == self.ETH_P_IPV6:
                    l4, offset = buf[6], 40
                    src = socket.inet_ntop(socket.AF_INET6, bytes(buf[8:24]))
                    dst = socket.inet_ntop(socket.AF_INET6, bytes(buf[24:40]))
                else:
                    continue
                if l4 not in (6, 17):
                    continue
                sport, dport = struct.unpack_from("!HH", buf, offset)
                yield src, sport, dst, dport, size, pkttype == self.PACKET_OUTGOING
        finally:
            sock.close()


def create_attributor() -> NetworkAttributor:
    if IS_WINDOWS:
        return WinDivertSniffer()
    if IS_LINUX:
        return AfPacketSniffer()
    return NullAttributor("per-process network attribution is not implemented on this platform yet")
