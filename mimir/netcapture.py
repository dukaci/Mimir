"""Per-process network attribution.

psutil cannot say which process sent or received a packet, so this module
counts bytes per PID by *passively sniffing* traffic and matching each packet
to a socket owner from psutil.net_connections().

Safety rule: the attributor must never sit in the packet path.  On Windows the
WinDivert driver is opened with the SNIFF flag, which hands us a copy of each
packet while the original continues untouched.  If Python falls behind, the
driver drops copies, never real traffic.  Packets are never re-injected.

Linux is not implemented yet; the base class documents the contract so a
raw-socket / nethogs-style backend can be added later.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

import psutil

from .platform import IS_WINDOWS, is_admin

log = logging.getLogger(__name__)

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


class WinDivertSniffer(NetworkAttributor):
    """Windows backend: WinDivert in SNIFF mode + psutil socket table."""

    def __init__(self):
        self.stats = CaptureStats()
        if not PYDIVERT_AVAILABLE:
            self.available, self.reason = False, "pydivert is not installed"
        elif not is_admin():
            self.available, self.reason = False, "needs administrator rights"
        else:
            self.available, self.reason = True, ""
        self._lock = threading.Lock()
        self._counters: dict[int, list] = {}     # pid -> [bytes_recv, bytes_sent, since]
        self._thread: threading.Thread | None = None
        self._active = False
        self._handle = None
        self._conn: dict[tuple, int] = {}
        self._local: dict[tuple, int] = {}
        self._port: dict[int, int] = {}

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
        h = self._handle
        if h is not None:
            try:
                h.close()        # unblocks the receive loop
            except Exception:
                pass

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
            lip, lport = c.laddr.ip, c.laddr.port
            if c.raddr:
                conn[(lip, lport, c.raddr.ip, c.raddr.port)] = c.pid
            local[(lip, lport)] = c.pid
            if lip in ("0.0.0.0", "::"):
                port[lport] = c.pid
        self._conn, self._local, self._port = conn, local, port

    def _map_loop(self) -> None:
        while self._active:
            self._refresh_maps()
            for _ in range(20):
                if not self._active:
                    return
                time.sleep(0.1)

    def _run(self) -> None:
        threading.Thread(target=self._map_loop, name="mimir-sockmap", daemon=True).start()
        self._refresh_maps()
        try:
            with pydivert.WinDivert("tcp or udp", flags=pydivert.Flag.SNIFF) as w:
                self._handle = w
                log.info("network capture started (passive sniff mode)")
                for packet in w:
                    if not self._active:
                        break
                    self.stats.packets += 1
                    self._account(packet)
        except Exception as e:
            if self._active:
                self.stats.error = str(e)
                log.error("network capture stopped: %s", e)
        finally:
            self._handle = None
            self._active = False
            log.info("network capture stopped")

    def _account(self, packet) -> None:
        try:
            if packet.ipv4:
                src, dst = str(packet.ipv4.src_addr), str(packet.ipv4.dst_addr)
            elif packet.ipv6:
                src, dst = str(packet.ipv6.src_addr), str(packet.ipv6.dst_addr)
            else:
                return
            if packet.tcp:
                sport, dport = packet.tcp.src_port, packet.tcp.dst_port
            elif packet.udp:
                sport, dport = packet.udp.src_port, packet.udp.dst_port
            else:
                return
            outbound = packet.is_outbound
            if outbound:
                local, key = (src, sport), (src, sport, dst, dport)
            else:
                local, key = (dst, dport), (dst, dport, src, sport)
            pid = self._conn.get(key) or self._local.get(local) or self._port.get(local[1])
            if not pid:
                return
            size = len(packet.raw)
            self.stats.attributed += 1
            with self._lock:
                c = self._counters.get(pid)
                if c is None:
                    c = self._counters[pid] = [0, 0, time.time()]
                c[1 if outbound else 0] += size
        except Exception:
            pass


def create_attributor() -> NetworkAttributor:
    if IS_WINDOWS:
        return WinDivertSniffer()
    return NullAttributor("per-process network attribution is not implemented on this platform yet")
