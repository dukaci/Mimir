"""Background sampler: turns psutil / NVML / capture data into Snapshots."""

from __future__ import annotations

import logging
import threading
import time

import psutil

from .gpu import GpuMonitor
from .history import HistoryStore, ProcSample, Snapshot
from .metrics import PROCESS_METRIC_INDEX, PROCESS_METRICS
from .netcapture import NetworkAttributor
from .settings import Settings
from .winproc import create_snapshot_source

log = logging.getLogger(__name__)

_I_CPU = PROCESS_METRIC_INDEX["cpu"]
_I_MEM = PROCESS_METRIC_INDEX["mem"]
_I_DR = PROCESS_METRIC_INDEX["disk_read"]
_I_DW = PROCESS_METRIC_INDEX["disk_write"]
_I_ND = PROCESS_METRIC_INDEX["net_down"]
_I_NU = PROCESS_METRIC_INDEX["net_up"]
_I_GPU = PROCESS_METRIC_INDEX["gpu"]
_I_GMEM = PROCESS_METRIC_INDEX["gpu_mem"]
_I_THR = PROCESS_METRIC_INDEX["threads"]
_N = len(PROCESS_METRICS)

_ATTRS = ["pid", "name", "cpu_percent", "memory_info", "io_counters", "num_threads"]


class Sampler:
    def __init__(self, settings: Settings, store: HistoryStore, net: NetworkAttributor,
                 gpu: GpuMonitor | None = None):
        self.settings = settings
        self.store = store
        self.net = net
        self.gpu = gpu or GpuMonitor()
        self.cpu_count = psutil.cpu_count(logical=True) or 1
        self.running = False
        self.paused = False
        self.last_error: str | None = None
        self.last_sample_ms = 0.0
        self.samples = 0
        self._thread: threading.Thread | None = None
        self._io_prev: dict[int, tuple[float, float, float]] = {}   # pid -> (read, write, ts)
        self._sys_prev: tuple[float, object, object] | None = None  # (ts, disk, net)
        self.mem_total = float(psutil.virtual_memory().total)
        # Fast path: one kernel call for every process (Windows). Falls back to psutil.
        self._kernel_snapshot = create_snapshot_source()
        self._cpu_prev: dict[int, tuple[float, float]] = {}        # pid -> (cpu_time, ts)
        self.backend = "kernel snapshot" if self._kernel_snapshot else "psutil"

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._loop, name="mimir-sampler", daemon=True)
        self._thread.start()
        if self.settings.network_capture:
            self.net.start()

    def stop(self) -> None:
        self.running = False
        self.net.stop()
        if self._thread:
            self._thread.join(timeout=3)
        self.gpu.shutdown()

    def set_paused(self, paused: bool) -> None:
        self.paused = paused

    # ----------------------------------------------------------------- loop
    def _loop(self) -> None:
        psutil.cpu_percent(interval=None)
        if self._kernel_snapshot is None:
            for p in psutil.process_iter(attrs=["cpu_percent"]):
                pass    # prime per-process CPU counters so the first sample is meaningful
        self._sleep(0.5)
        while self.running:
            try:
                if not self.paused:
                    t0 = time.perf_counter()
                    self.sample_once()
                    self.last_sample_ms = (time.perf_counter() - t0) * 1000
                    self.last_error = None
            except Exception as e:      # keep sampling; surface the error in the UI
                self.last_error = f"{type(e).__name__}: {e}"
                log.exception("sampling failed")
            self._sleep(self.settings.sample_interval)

    def _sleep(self, seconds: float) -> None:
        deadline = time.time() + seconds
        while self.running:
            left = deadline - time.time()
            if left <= 0:
                return
            time.sleep(min(0.1, left))

    # --------------------------------------------------------------- sample
    def sample_once(self) -> Snapshot:
        now = time.time()
        net_rates = self.net.take(now) if self.net.active else {}
        gpu_util, self._gpu_system = self.gpu.sample()
        threshold = self.settings.cpu_threshold
        procs: dict[int, ProcSample] = {}
        io_now: dict[int, tuple[float, float, float]] = {}

        if self._kernel_snapshot is not None:
            self._collect_kernel(now, procs, io_now, net_rates, gpu_util, threshold)
        else:
            self._collect_psutil(now, procs, io_now, net_rates, gpu_util, threshold)

        self._io_prev = io_now
        snap = Snapshot(ts=now, procs=procs, system=self._system(now))
        self.store.append(snap)
        self.samples += 1
        return snap

    def _collect_kernel(self, now, procs, io_now, net_rates, gpu_util, threshold) -> None:
        cpu_prev = self._cpu_prev
        cpu_now: dict[int, tuple[float, float]] = {}
        for pid, info in self._kernel_snapshot().items():
            cpu_now[pid] = (info.cpu_time, now)
            values = [0.0] * _N
            prev = cpu_prev.get(pid)
            cpu_core = 0.0
            if prev is not None:
                dt = now - prev[1]
                if dt > 0:
                    cpu_core = max(0.0, info.cpu_time - prev[0]) / dt * 100.0     # % of one core
            values[_I_CPU] = cpu_core / self.cpu_count
            values[_I_MEM] = float(info.private_ws)
            io_now[pid] = (float(info.read_bytes), float(info.write_bytes), now)
            prev_io = self._io_prev.get(pid)
            if prev_io is not None:
                dt = now - prev_io[2]
                if dt > 0:
                    values[_I_DR] = max(0.0, info.read_bytes - prev_io[0]) / dt
                    values[_I_DW] = max(0.0, info.write_bytes - prev_io[1]) / dt
            nr = net_rates.get(pid)
            if nr:
                values[_I_ND], values[_I_NU] = nr
            g = gpu_util.get(pid)
            if g is not None:
                values[_I_GPU], values[_I_GMEM] = g.util, g.mem
            values[_I_THR] = float(info.threads)
            if threshold > 0 and cpu_core < threshold and not any(
                    values[i] for i in (_I_DR, _I_DW, _I_ND, _I_NU, _I_GPU)):
                continue
            procs[pid] = ProcSample(pid, info.name, values)
        self._cpu_prev = cpu_now

    def _collect_psutil(self, now, procs, io_now, net_rates, gpu_util, threshold) -> None:
        for p in psutil.process_iter(attrs=_ATTRS, ad_value=None):
            info = p.info
            pid = info["pid"]
            if pid == 0 and not info.get("name"):
                continue
            name = info.get("name") or f"pid {pid}"
            cpu_core = info.get("cpu_percent") or 0.0
            values = [0.0] * _N
            values[_I_CPU] = cpu_core / self.cpu_count
            mem = info.get("memory_info")
            if mem is not None:
                values[_I_MEM] = float(getattr(mem, "private", None) or mem.rss)
            io = info.get("io_counters")
            if io is not None:
                io_now[pid] = (float(io.read_bytes), float(io.write_bytes), now)
                prev = self._io_prev.get(pid)
                if prev is not None:
                    dt = now - prev[2]
                    if dt > 0:
                        values[_I_DR] = max(0.0, io.read_bytes - prev[0]) / dt
                        values[_I_DW] = max(0.0, io.write_bytes - prev[1]) / dt
            nr = net_rates.get(pid)
            if nr:
                values[_I_ND], values[_I_NU] = nr
            g = gpu_util.get(pid)
            if g is not None:
                values[_I_GPU], values[_I_GMEM] = g.util, g.mem
            values[_I_THR] = float(info.get("num_threads") or 0)

            if threshold > 0 and cpu_core < threshold and not any(
                    values[i] for i in (_I_DR, _I_DW, _I_ND, _I_NU, _I_GPU)):
                continue        # user asked to ignore quiet processes
            procs[pid] = ProcSample(pid, name, values)

    def _system(self, now: float) -> dict[str, float]:
        vm = psutil.virtual_memory()
        disk = psutil.disk_io_counters()
        net = psutil.net_io_counters()
        sysd = {
            "cpu": float(psutil.cpu_percent(interval=None)),
            "mem": float(vm.used),
            "disk_read": 0.0, "disk_write": 0.0, "net_down": 0.0, "net_up": 0.0,
            "gpu": 0.0, "gpu_mem": 0.0,
        }
        prev = self._sys_prev
        if prev is not None:
            dt = now - prev[0]
            if dt > 0:
                if disk and prev[1]:
                    sysd["disk_read"] = max(0.0, disk.read_bytes - prev[1].read_bytes) / dt
                    sysd["disk_write"] = max(0.0, disk.write_bytes - prev[1].write_bytes) / dt
                if net and prev[2]:
                    sysd["net_down"] = max(0.0, net.bytes_recv - prev[2].bytes_recv) / dt
                    sysd["net_up"] = max(0.0, net.bytes_sent - prev[2].bytes_sent) / dt
        self._sys_prev = (now, disk, net)
        g = getattr(self, "_gpu_system", None)
        if g:
            sysd["gpu"] = g.util
            sysd["gpu_mem"] = g.mem_used
        return sysd
