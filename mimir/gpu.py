"""GPU statistics.

Two sources are combined:

* NVML (nvidia-ml-py) for whole-GPU utilisation, VRAM used/total and the
  adapter name.  On Linux it also reports per-process VRAM and utilisation.
* Windows performance counters ("GPU Engine" and "GPU Process Memory", the
  same data Task Manager shows) for per-process utilisation and dedicated
  VRAM.  They work for every vendor, need no admin rights, and cost well under
  a millisecond per sample once the query exists.  NVML cannot report
  per-process VRAM on Windows (WDDM hides it), which is why counters are used.

Everything degrades to "no data" when a source is missing.
"""

from __future__ import annotations

import logging
import re
import threading
from collections import defaultdict
from dataclasses import dataclass

from .platform import IS_WINDOWS

log = logging.getLogger(__name__)

try:
    import pynvml
    _NVML_IMPORTED = True
except ImportError:      # pragma: no cover
    pynvml = None
    _NVML_IMPORTED = False

try:
    import win32pdh
    _PDH_IMPORTED = True
except ImportError:      # pragma: no cover
    win32pdh = None
    _PDH_IMPORTED = False

_PID_RE = re.compile(r"pid_(\d+)_")
_ENG_RE = re.compile(r"engtype_(\w+)")


@dataclass
class GpuSystem:
    util: float          # percent
    mem_used: float      # bytes
    mem_total: float     # bytes, 0 when unknown
    name: str


@dataclass
class ProcGpu:
    util: float = 0.0    # percent of the busiest engine (Task Manager's "GPU" column)
    mem: float = 0.0     # dedicated VRAM, bytes


class NvmlSource:
    def __init__(self):
        self.available = False
        self.reason = "nvidia-ml-py not installed" if not _NVML_IMPORTED else ""
        self.name = ""
        self._handle = None
        self._last_util_ts = 0
        self._lock = threading.Lock()
        if _NVML_IMPORTED:
            try:
                pynvml.nvmlInit()
                if pynvml.nvmlDeviceGetCount() < 1:
                    self.reason = "no NVIDIA GPU"
                    return
                self._handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                name = pynvml.nvmlDeviceGetName(self._handle)
                self.name = name.decode() if isinstance(name, bytes) else str(name)
                self.available = True
            except Exception as e:
                self.reason = f"NVML unavailable ({e})"

    def system(self) -> GpuSystem | None:
        if not self.available:
            return None
        try:
            with self._lock:
                util = pynvml.nvmlDeviceGetUtilizationRates(self._handle)
                mem = pynvml.nvmlDeviceGetMemoryInfo(self._handle)
            return GpuSystem(float(util.gpu), float(mem.used), float(mem.total), self.name)
        except Exception as e:
            log.debug("NVML system query failed: %s", e)
            return None

    def processes(self) -> dict[int, ProcGpu]:
        """Per-process utilisation (all platforms) and VRAM (Linux only)."""
        if not self.available:
            return {}
        out: dict[int, ProcGpu] = defaultdict(ProcGpu)
        try:
            with self._lock:
                samples = pynvml.nvmlDeviceGetProcessUtilization(self._handle, self._last_util_ts)
            newest = self._last_util_ts
            for s in samples:
                newest = max(newest, s.timeStamp)
                util = float(s.smUtil) + float(getattr(s, "encUtil", 0)) + float(getattr(s, "decUtil", 0))
                out[s.pid].util = max(out[s.pid].util, min(100.0, util))
            self._last_util_ts = newest
        except Exception as e:      # NOT_FOUND when nothing ran since last time
            log.debug("NVML process utilisation: %s", e)
        try:
            with self._lock:
                procs = list(pynvml.nvmlDeviceGetGraphicsRunningProcesses(self._handle))
                procs += list(pynvml.nvmlDeviceGetComputeRunningProcesses(self._handle))
            for p in procs:
                if p.usedGpuMemory:      # None on Windows
                    out[p.pid].mem = max(out[p.pid].mem, float(p.usedGpuMemory))
        except Exception as e:
            log.debug("NVML process memory: %s", e)
        return dict(out)

    def shutdown(self) -> None:
        if self.available:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass


class PdhGpuSource:
    """Windows performance counters for per-process GPU engine use and VRAM."""

    def __init__(self):
        self.available = False
        self.reason = "pywin32 not installed" if not _PDH_IMPORTED else ""
        self._query = None
        self._lock = threading.Lock()
        self._primed = False
        if not (_PDH_IMPORTED and IS_WINDOWS):
            return
        try:
            self._query = win32pdh.OpenQuery()
            self._h_util = win32pdh.AddEnglishCounter(self._query, r"\GPU Engine(*)\Utilization Percentage")
            self._h_mem = win32pdh.AddEnglishCounter(self._query, r"\GPU Process Memory(*)\Dedicated Usage")
            self._h_adapter = win32pdh.AddEnglishCounter(self._query, r"\GPU Adapter Memory(*)\Dedicated Usage")
            win32pdh.CollectQueryData(self._query)      # first collection primes rate counters
            self.available = True
        except Exception as e:
            self.reason = f"GPU performance counters unavailable ({e})"
            log.info("PDH GPU counters disabled: %s", self.reason)

    def collect(self) -> tuple[dict[int, ProcGpu], float, float]:
        """Returns (per-process stats, whole-GPU utilisation %, adapter VRAM used)."""
        if not self.available:
            return {}, 0.0, 0.0
        try:
            with self._lock:
                win32pdh.CollectQueryData(self._query)
                util = win32pdh.GetFormattedCounterArray(self._h_util, win32pdh.PDH_FMT_DOUBLE)
                mem = win32pdh.GetFormattedCounterArray(self._h_mem, win32pdh.PDH_FMT_LARGE)
                adapter = win32pdh.GetFormattedCounterArray(self._h_adapter, win32pdh.PDH_FMT_LARGE)
        except Exception as e:      # PDH_NO_DATA on the first collection, or a counter vanished
            log.debug("PDH collect: %s", e)
            return {}, 0.0, 0.0
        procs: dict[int, ProcGpu] = defaultdict(ProcGpu)
        engine_totals: dict[str, float] = defaultdict(float)
        for inst, v in util.items():
            m = _PID_RE.search(inst)
            if not m:
                continue
            pid = int(m.group(1))
            eng = _ENG_RE.search(inst)
            engine_totals[eng.group(1) if eng else "?"] += v
            procs[pid].util = max(procs[pid].util, min(100.0, float(v)))
        for inst, v in mem.items():
            m = _PID_RE.search(inst)
            if m:
                procs[int(m.group(1))].mem += float(v)
        system_util = min(100.0, max(engine_totals.values(), default=0.0))
        adapter_used = float(sum(adapter.values()))
        return dict(procs), system_util, adapter_used


class GpuMonitor:
    """Facade used by the sampler: best available source for each figure."""

    def __init__(self):
        self.nvml = NvmlSource()
        self.pdh = PdhGpuSource() if IS_WINDOWS else None
        self.name = self.nvml.name or ("GPU (performance counters)" if self.pdh and self.pdh.available else "")
        self.available = self.nvml.available or bool(self.pdh and self.pdh.available)
        self.per_process = bool(self.pdh and self.pdh.available) or (self.nvml.available and not IS_WINDOWS)
        self.per_process_mem = self.per_process
        self.reason = "" if self.available else (self.nvml.reason if not self.pdh else f"{self.nvml.reason}; {self.pdh.reason}")
        self.source = "counters" if (self.pdh and self.pdh.available) else ("nvml" if self.nvml.available else "none")
        self._last_pdh: tuple[dict[int, ProcGpu], float, float] = ({}, 0.0, 0.0)

    def sample(self) -> tuple[dict[int, ProcGpu], GpuSystem | None]:
        """One call per tick: per-process stats and whole-GPU totals."""
        procs: dict[int, ProcGpu] = {}
        pdh_util = pdh_mem = 0.0
        if self.pdh and self.pdh.available:
            procs, pdh_util, pdh_mem = self.pdh.collect()
        elif self.nvml.available:
            procs = self.nvml.processes()
        system = self.nvml.system() if self.nvml.available else None
        if system is None and self.pdh and self.pdh.available:
            system = GpuSystem(pdh_util, pdh_mem, 0.0, self.name)
        return procs, system

    def shutdown(self) -> None:
        self.nvml.shutdown()
