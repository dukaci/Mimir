"""Thread-safe time-series store for process and system samples.

The store keeps a bounded deque of snapshots (one per sampling tick) and, for
every entity it has ever seen, an all-time peak per metric.  Entities are
either a single PID or every process sharing a name (a "group").

Only plain data lives here; nothing in this module touches the UI.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .metrics import PROCESS_METRICS, PROCESS_METRIC_INDEX, SYSTEM_METRICS

N_METRICS = len(PROCESS_METRICS)
IDLE_NAMES = {"system idle process", "idle"}

EntityKey = tuple[str, object]   # ("pid", 1234) or ("name", "chrome.exe")


class ProcSample:
    __slots__ = ("pid", "name", "values")

    def __init__(self, pid: int, name: str, values: list[float]):
        self.pid = pid
        self.name = name
        self.values = values      # indexed by PROCESS_METRIC_INDEX


@dataclass(slots=True)
class Snapshot:
    ts: float                                # epoch seconds
    procs: dict[int, ProcSample]
    system: dict[str, float]                 # keyed by SYSTEM metric id


@dataclass(slots=True)
class Peak:
    value: float
    ts: float


@dataclass
class EntityRow:
    """One row of the ranking table."""
    key: EntityKey
    name: str
    pids: list[int]
    alive: bool
    current: list[float]                     # last sample
    average: list[float]                     # over the ranking window
    maximum: list[float]                     # over the ranking window
    peaks: list[Optional[Peak]]              # all-time, since the store started
    samples: int = 0

    @property
    def label(self) -> str:
        return f"{self.name} ({len(self.pids)})" if len(self.pids) > 1 else self.name


@dataclass
class SeriesBundle:
    ts: list[float]
    values: dict[str, list[float]] = field(default_factory=dict)


class HistoryStore:
    def __init__(self, maxlen: int):
        self._lock = threading.Lock()
        self._snapshots: deque[Snapshot] = deque(maxlen=max(2, maxlen))
        self._peaks: dict[EntityKey, list[Optional[Peak]]] = {}
        self._system_peaks: dict[str, Peak] = {}
        self._last_seen: dict[int, float] = {}
        self._ticks = 0
        self.started_at = time.time()

    # ----------------------------------------------------------- mutation
    @property
    def maxlen(self) -> int:
        return self._snapshots.maxlen or 0

    def set_maxlen(self, maxlen: int) -> None:
        maxlen = max(2, maxlen)
        with self._lock:
            if maxlen != self._snapshots.maxlen:
                self._snapshots = deque(self._snapshots, maxlen=maxlen)

    def clear(self) -> None:
        with self._lock:
            self._snapshots.clear()
            self._peaks.clear()
            self._system_peaks.clear()
            self._last_seen.clear()
            self.started_at = time.time()

    def append(self, snap: Snapshot) -> None:
        with self._lock:
            self._snapshots.append(snap)
            self._ticks += 1
            self._update_peaks(snap)
            if self._ticks % 120 == 0:
                self._prune(snap.ts)

    def _update_peaks(self, snap: Snapshot) -> None:
        by_name: dict[str, list[float]] = {}
        for pid, ps in snap.procs.items():
            self._last_seen[pid] = snap.ts
            self._bump(("pid", pid), ps.values, snap.ts)
            agg = by_name.get(ps.name)
            if agg is None:
                by_name[ps.name] = list(ps.values)
            else:
                for i, v in enumerate(ps.values):
                    agg[i] += v
        for name, vals in by_name.items():
            self._bump(("name", name), vals, snap.ts)
        for mid, v in snap.system.items():
            p = self._system_peaks.get(mid)
            if p is None or v > p.value:
                self._system_peaks[mid] = Peak(v, snap.ts)

    def _bump(self, key: EntityKey, values: list[float], ts: float) -> None:
        peaks = self._peaks.get(key)
        if peaks is None:
            self._peaks[key] = [Peak(v, ts) for v in values]
            return
        for i, v in enumerate(values):
            p = peaks[i]
            if p is None or v > p.value:
                peaks[i] = Peak(v, ts)

    def _prune(self, now: float) -> None:
        horizon = now - 2 * self.maxlen * 10   # generous: keep dead PIDs a while for peaks
        dead = [pid for pid, seen in self._last_seen.items() if seen < horizon]
        for pid in dead:
            self._last_seen.pop(pid, None)
            self._peaks.pop(("pid", pid), None)

    # -------------------------------------------------------------- queries
    def __len__(self) -> int:
        with self._lock:
            return len(self._snapshots)

    def latest(self) -> Optional[Snapshot]:
        with self._lock:
            return self._snapshots[-1] if self._snapshots else None

    def window(self, seconds: float, now: Optional[float] = None) -> list[Snapshot]:
        """Snapshots from the last `seconds`, oldest first."""
        now = time.time() if now is None else now
        cutoff = now - seconds
        with self._lock:
            out = []
            for snap in reversed(self._snapshots):
                if snap.ts >= cutoff:
                    out.append(snap)
                else:
                    break
        out.reverse()
        return out

    def span_seconds(self) -> float:
        with self._lock:
            if len(self._snapshots) < 2:
                return 0.0
            return self._snapshots[-1].ts - self._snapshots[0].ts

    def peaks_for(self, key: EntityKey) -> list[Optional[Peak]]:
        with self._lock:
            p = self._peaks.get(key)
            return list(p) if p else [None] * N_METRICS

    def system_peak(self, metric_id: str) -> Optional[Peak]:
        with self._lock:
            return self._system_peaks.get(metric_id)

    def entity_series(self, key: EntityKey, metric_ids: Iterable[str], seconds: float,
                      pids: Optional[Iterable[int]] = None) -> SeriesBundle:
        """Per-tick values for an entity. Group entities are summed across PIDs."""
        snaps = self.window(seconds)
        metric_ids = list(metric_ids)
        idx = [PROCESS_METRIC_INDEX[m] for m in metric_ids]
        bundle = SeriesBundle(ts=[s.ts for s in snaps], values={m: [] for m in metric_ids})
        kind, ident = key
        pid_set = set(pids) if pids is not None else None
        for snap in snaps:
            totals = [0.0] * len(idx)
            if kind == "pid":
                ps = snap.procs.get(ident)
                if ps is not None:
                    for j, i in enumerate(idx):
                        totals[j] = ps.values[i]
            else:
                for pid, ps in snap.procs.items():
                    if ps.name == ident or (pid_set is not None and pid in pid_set):
                        for j, i in enumerate(idx):
                            totals[j] += ps.values[i]
            for j, m in enumerate(metric_ids):
                bundle.values[m].append(totals[j])
        return bundle

    def multi_entity_series(self, entities: list[tuple[EntityKey, Iterable[int]]], metric_id: str,
                            seconds: float) -> tuple[list[float], list[list[float]]]:
        """One metric for several entities in a single pass over the window."""
        snaps = self.window(seconds)
        mi = PROCESS_METRIC_INDEX[metric_id]
        by_pid: dict[int, int] = {}
        by_name: dict[str, int] = {}
        for i, (key, pids) in enumerate(entities):
            if key[0] == "name":
                by_name[key[1]] = i
            else:
                for pid in pids:
                    by_pid[pid] = i
        out = [[0.0] * len(snaps) for _ in entities]
        for t, snap in enumerate(snaps):
            for pid, ps in snap.procs.items():
                i = by_pid.get(pid)
                if i is None:
                    i = by_name.get(ps.name)
                if i is not None:
                    out[i][t] += ps.values[mi]
        return [s.ts for s in snaps], out

    def system_series(self, metric_ids: Iterable[str], seconds: float) -> SeriesBundle:
        snaps = self.window(seconds)
        metric_ids = list(metric_ids)
        bundle = SeriesBundle(ts=[s.ts for s in snaps], values={m: [] for m in metric_ids})
        for snap in snaps:
            for m in metric_ids:
                bundle.values[m].append(snap.system.get(m, 0.0))
        return bundle

    def rankings(self, seconds: float, grouped: bool, hide_idle: bool = True,
                 name_filter: str = "", sort_metric: str = "cpu", sort_field: str = "current",
                 descending: bool = True, limit: int = 50, only_alive: bool = False) -> list[EntityRow]:
        """Aggregate the ranking window into one row per entity."""
        snaps = self.window(seconds)
        if not snaps:
            return []
        last = snaps[-1]
        name_filter = name_filter.lower().strip()
        acc: dict[EntityKey, dict] = {}

        for snap in snaps:
            per: dict[EntityKey, tuple[list[float], set[int], str]] = {}
            for pid, ps in snap.procs.items():
                lname = ps.name.lower()
                if hide_idle and lname in IDLE_NAMES:
                    continue
                if name_filter and name_filter not in lname:
                    continue
                key = ("name", ps.name) if grouped else ("pid", pid)
                row = per.get(key)
                if row is None:
                    per[key] = (list(ps.values), {pid}, ps.name)
                else:
                    vals = row[0]
                    for i, v in enumerate(ps.values):
                        vals[i] += v
                    row[1].add(pid)
            is_last = snap is last
            for key, (vals, pids, name) in per.items():
                a = acc.get(key)
                if a is None:
                    a = acc[key] = {"name": name, "pids": set(), "sum": [0.0] * N_METRICS,
                                    "max": [0.0] * N_METRICS, "cur": [0.0] * N_METRICS,
                                    "alive": False, "n": 0}
                a["pids"] |= pids
                a["n"] += 1
                s, mx = a["sum"], a["max"]
                for i, v in enumerate(vals):
                    s[i] += v
                    if v > mx[i]:
                        mx[i] = v
                if is_last:
                    a["cur"] = vals
                    a["alive"] = True

        n = len(snaps)
        with self._lock:
            rows = []
            for key, a in acc.items():
                if only_alive and not a["alive"]:
                    continue
                peaks = self._peaks.get(key)
                rows.append(EntityRow(
                    key=key, name=a["name"], pids=sorted(a["pids"]), alive=a["alive"],
                    current=a["cur"], average=[v / n for v in a["sum"]], maximum=a["max"],
                    peaks=list(peaks) if peaks else [None] * N_METRICS, samples=a["n"],
                ))

        mi = PROCESS_METRIC_INDEX.get(sort_metric, 0)

        def sort_key(r: EntityRow) -> float:
            if sort_field == "peak":
                p = r.peaks[mi]
                return p.value if p else 0.0
            if sort_field == "average":
                return r.average[mi]
            if sort_field == "maximum":
                return r.maximum[mi]
            return r.current[mi]

        rows.sort(key=sort_key, reverse=descending)
        return rows[:limit]


def system_metric_ids() -> list[str]:
    return [m.id for m in SYSTEM_METRICS]
