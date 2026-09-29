"""Metric definitions and value formatting shared by the sampler, store and UI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# Catppuccin Mocha accents used consistently for each metric
BLUE = (137, 180, 250)
MAUVE = (203, 166, 247)
GREEN = (166, 227, 161)
PEACH = (250, 179, 135)
TEAL = (148, 226, 213)
PINK = (245, 194, 231)
YELLOW = (249, 226, 175)
LAVENDER = (180, 190, 254)
RED = (243, 139, 168)
SKY = (137, 220, 235)


@dataclass(frozen=True)
class Metric:
    id: str
    label: str          # long name for headings
    short: str          # column header
    kind: str           # "percent" | "bytes" | "rate" | "count"
    color: tuple[int, int, int]
    description: str = ""


# Per-process metrics. Order matters: Snapshot.values is indexed by position.
PROCESS_METRICS: list[Metric] = [
    Metric("cpu", "CPU", "CPU", "percent", BLUE, "share of the whole machine's CPU time"),
    Metric("mem", "Memory", "Memory", "bytes", MAUVE, "private working set"),
    Metric("disk_read", "Disk read", "Disk R", "rate", GREEN, "bytes read per second"),
    Metric("disk_write", "Disk write", "Disk W", "rate", PEACH, "bytes written per second"),
    Metric("net_down", "Download", "Down", "rate", TEAL, "bytes received per second"),
    Metric("net_up", "Upload", "Up", "rate", PINK, "bytes sent per second"),
    Metric("gpu", "GPU", "GPU", "percent", YELLOW, "busiest GPU engine utilisation"),
    Metric("gpu_mem", "VRAM", "VRAM", "bytes", LAVENDER, "dedicated GPU memory"),
    Metric("threads", "Threads", "Threads", "count", SKY, "thread count"),
]
PROCESS_METRIC_INDEX: dict[str, int] = {m.id: i for i, m in enumerate(PROCESS_METRICS)}
PROCESS_METRIC_BY_ID: dict[str, Metric] = {m.id: m for m in PROCESS_METRICS}

# System-wide metrics (keyed by id in Snapshot.system)
SYSTEM_METRICS: list[Metric] = [
    Metric("cpu", "CPU", "CPU", "percent", BLUE, "total CPU utilisation"),
    Metric("mem", "Memory", "Memory", "bytes", MAUVE, "physical memory in use"),
    Metric("disk_read", "Disk read", "Disk R", "rate", GREEN, "all disks, bytes read per second"),
    Metric("disk_write", "Disk write", "Disk W", "rate", PEACH, "all disks, bytes written per second"),
    Metric("net_down", "Download", "Down", "rate", TEAL, "all interfaces, bytes received per second"),
    Metric("net_up", "Upload", "Up", "rate", PINK, "all interfaces, bytes sent per second"),
    Metric("gpu", "GPU", "GPU", "percent", YELLOW, "GPU utilisation"),
    Metric("gpu_mem", "GPU memory", "VRAM", "bytes", LAVENDER, "GPU memory in use"),
]
SYSTEM_METRIC_BY_ID: dict[str, Metric] = {m.id: m for m in SYSTEM_METRICS}


@dataclass(frozen=True)
class ChartGroup:
    """A set of metrics that share one axis and are charted together."""
    id: str
    label: str
    metrics: tuple[str, ...]
    system_metrics: tuple[str, ...]


CHART_GROUPS: list[ChartGroup] = [
    ChartGroup("cpu", "CPU", ("cpu",), ("cpu",)),
    ChartGroup("mem", "Memory", ("mem",), ("mem",)),
    ChartGroup("disk", "Disk", ("disk_read", "disk_write"), ("disk_read", "disk_write")),
    ChartGroup("net", "Network", ("net_down", "net_up"), ("net_down", "net_up")),
    ChartGroup("gpu", "GPU", ("gpu", "gpu_mem"), ("gpu", "gpu_mem")),
]
CHART_GROUP_BY_ID: dict[str, ChartGroup] = {g.id: g for g in CHART_GROUPS}

# Tiles shown in the header, each opens a chart group
TILES: list[tuple[str, str, tuple[str, ...]]] = [
    ("cpu", "CPU", ("cpu",)),
    ("mem", "Memory", ("mem",)),
    ("disk", "Disk", ("disk_read", "disk_write")),
    ("net", "Network", ("net_down", "net_up")),
    ("gpu", "GPU", ("gpu", "gpu_mem")),
]

_UNITS = ["B", "KB", "MB", "GB", "TB"]


def format_bytes(value: float, decimals: int = 1) -> str:
    value = float(value)
    for unit in _UNITS:
        if value < 1024 or unit == _UNITS[-1]:
            if unit == "B":
                return f"{value:.0f} B"
            return f"{value:.{decimals}f} {unit}"
        value /= 1024
    return f"{value:.{decimals}f} TB"


def format_rate(value: float) -> str:
    return format_bytes(value) + "/s"


def format_value(metric: Metric, value: float) -> str:
    if metric.kind == "percent":
        return f"{value:.1f}%"
    if metric.kind == "bytes":
        return format_bytes(value)
    if metric.kind == "rate":
        return format_rate(value)
    return f"{value:.0f}"


def axis_label(metric: Metric) -> str:
    return {"percent": "%", "bytes": "MB", "rate": "KB/s", "count": ""}[metric.kind]


def axis_scale(metric: Metric) -> float:
    """Multiplier applied to raw values so the axis reads in axis_label units."""
    return {"percent": 1.0, "bytes": 1 / 1024 ** 2, "rate": 1 / 1024, "count": 1.0}[metric.kind]


def format_clock(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")
