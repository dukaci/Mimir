"""User settings, persisted as JSON next to the package."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, fields
from pathlib import Path

log = logging.getLogger(__name__)


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


@dataclass
class Settings:
    # sampling
    sample_interval: float = 1.0        # seconds between samples
    history_seconds: int = 1800         # how much history to keep (30 min)
    cpu_threshold: float = 0.0          # % of one core below which a process is not stored
    network_capture: bool = True        # passive per-process network attribution (admin only)

    # ranking / table
    ranking_window: int = 30            # seconds averaged for avg/max columns
    group_by_name: bool = True
    hide_idle: bool = True
    table_rows: int = 40
    sort_metric: str = "cpu"
    sort_field: str = "current"         # current | average | maximum | peak
    sort_descending: bool = True

    # chart
    chart_seconds: int = 120
    chart_lines: int = 8                # top-N entities when nothing is selected
    chart_group: str = "cpu"

    # window
    window_width: int = 1600
    window_height: int = 960
    left_panel_width: int = 760

    def validate(self) -> "Settings":
        self.sample_interval = _clamp(float(self.sample_interval), 0.2, 10.0)
        self.history_seconds = _clamp(int(self.history_seconds), 60, 24 * 3600)
        self.cpu_threshold = _clamp(float(self.cpu_threshold), 0.0, 100.0)
        self.ranking_window = _clamp(int(self.ranking_window), 2, 3600)
        self.table_rows = _clamp(int(self.table_rows), 5, 500)
        self.chart_seconds = _clamp(int(self.chart_seconds), 10, 24 * 3600)
        self.chart_lines = _clamp(int(self.chart_lines), 1, 10)
        self.window_width = _clamp(int(self.window_width), 900, 8000)
        self.window_height = _clamp(int(self.window_height), 600, 5000)
        self.left_panel_width = _clamp(int(self.left_panel_width), 420, 3000)
        if self.sort_field not in ("current", "average", "maximum", "peak"):
            self.sort_field = "current"
        return self

    @property
    def max_snapshots(self) -> int:
        return int(self.history_seconds / self.sample_interval) + 2

    @classmethod
    def load(cls, path: Path) -> "Settings":
        s = cls()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                known = {f.name for f in fields(cls)}
                for k, v in data.items():
                    if k in known:
                        setattr(s, k, v)
            except (OSError, ValueError) as e:
                log.warning("Could not read %s: %s", path, e)
        return s.validate()

    def save(self, path: Path) -> None:
        try:
            path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        except OSError as e:
            log.warning("Could not save %s: %s", path, e)
