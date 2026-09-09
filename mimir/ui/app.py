"""DearPyGui front end.

All DearPyGui calls happen on the render thread inside App.run(); the sampler
only writes to the HistoryStore, and the UI polls it on a fixed cadence.
"""

from __future__ import annotations

import csv
import logging
import os
import time
from datetime import datetime
from typing import Optional

import dearpygui.dearpygui as dpg

from .. import __version__
from ..gpu import GpuMonitor
from ..history import EntityKey, EntityRow, HistoryStore
from ..killer import kill_processes
from ..metrics import (CHART_GROUPS, CHART_GROUP_BY_ID, PROCESS_METRIC_BY_ID, PROCESS_METRIC_INDEX,
                       SYSTEM_METRIC_BY_ID, TILES, Metric, axis_label, axis_scale, format_clock,
                       format_value)
from ..netcapture import NetworkAttributor
from ..platform import (ASSET_DIR, EXPORT_DIR, ICON_FILE, SETTINGS_FILE, can_elevate, elevation_hint,
                        relaunch_elevated, style_title_bar, viewport_handle, window_alive)
from ..sampler import Sampler
from ..settings import Settings
from . import theme as T
from .theme import Fonts, bind_font, series_theme

log = logging.getLogger(__name__)

WINDOW_TITLE = "Mimir"

TABLE_COLUMNS = [
    # (header, metric id, field) - field None means a non-sortable text column
    ("CPU", "cpu", "current"),
    ("CPU peak", "cpu", "peak"),
    ("Memory", "mem", "current"),
    ("Mem peak", "mem", "peak"),
    ("Disk R", "disk_read", "current"),
    ("Disk W", "disk_write", "current"),
    ("Down", "net_down", "current"),
    ("Up", "net_up", "current"),
    ("GPU", "gpu", "current"),
    ("VRAM", "gpu_mem", "current"),
    ("VRAM peak", "gpu_mem", "peak"),
]


class App:
    def __init__(self, settings: Settings, store: HistoryStore, sampler: Sampler,
                 net: NetworkAttributor, gpu: GpuMonitor):
        self.settings = settings
        self.store = store
        self.sampler = sampler
        self.net = net
        self.gpu = gpu

        self.rows: list[EntityRow] = []
        self.selection: Optional[dict] = None       # {"key", "label", "pids"}, kept across scope changes
        self.scope = "top"                          # "system" | "top" | "selected"
        self.chart_group = settings.chart_group if settings.chart_group in CHART_GROUP_BY_ID else "cpu"
        self.name_filter = ""
        self.status_message = ""
        self.status_until = 0.0
        self.restart_elevated = False

        # DearPyGui keeps axis limits as 32-bit floats, so chart x values are seconds
        # since t0 (small numbers) and tick labels are rendered as wall-clock time.
        self.t0 = float(int(time.time()) // 60 * 60)
        self._tile_series: dict[str, int] = {}
        self._series: dict[str, dict] = {"": {}, "_b": {}}     # per plot: series key -> line tag
        self._ticks_key = None
        self._split = False
        self._plot_height = None
        self.top_rows_by: dict[str, list[EntityRow]] = {}
        self.chart_colors: dict = {}
        self._color_slots: dict = {}                # entity key -> [palette index, last seen]
        self._row_items: list[tuple[int, EntityRow]] = []
        self._kill_target: Optional[dict] = None    # {"name", "pids", "key"} awaiting confirmation
        self._next_table = 0.0
        self._next_chart = 0.0
        self._next_tiles = 0.0
        self._next_footer = 0.0

    # ================================================================ build
    def build(self) -> None:
        dpg.create_context()
        T.setup_fonts()
        T.setup_themes()
        s = self.settings

        with dpg.window(tag="main_window", no_scrollbar=True):
            self._build_header()
            dpg.add_spacer(height=2)
            self._build_tiles()
            dpg.add_spacer(height=2)
            with dpg.group(horizontal=True):
                self._build_table_panel()
                self._build_chart_panel()
            f = dpg.add_text("", tag="footer_text", color=T.OVERLAY0)
            bind_font(f, "font_small")

        self._build_settings_window()
        self._build_kill_window()

        icon = str(ICON_FILE) if ICON_FILE.exists() else ""
        dpg.create_viewport(title=WINDOW_TITLE, width=s.window_width, height=s.window_height,
                            min_width=1000, min_height=650, clear_color=T.MANTLE,
                            small_icon=icon, large_icon=icon)
        dpg.setup_dearpygui()
        dpg.show_viewport()
        dpg.set_primary_window("main_window", True)
        # Native title bar in theme colours (no-op outside Windows)
        style_title_bar(WINDOW_TITLE, caption_rgb=T.MANTLE, text_rgb=T.TEXT, border_rgb=T.SURFACE0)

    def _build_header(self) -> None:
        with dpg.group(horizontal=True):
            logo = ASSET_DIR / "mimir.png"
            if logo.exists():
                try:
                    w, h, _c, data = dpg.load_image(str(logo))
                    with dpg.texture_registry():
                        dpg.add_static_texture(w, h, data, tag="logo_tex")
                    dpg.add_image("logo_tex", width=30, height=30)
                    dpg.add_spacer(width=2)
                except Exception as e:      # a missing or unreadable image must not stop the app
                    log.debug("logo not loaded: %s", e)
            t = dpg.add_text("Mimir")
            bind_font(t, "font_title")
            dpg.add_spacer(width=4)
            dpg.add_text("resource monitor", color=T.SUBTEXT)
            dpg.add_spacer(width=20)
            dpg.add_text("", tag="status_text")
            dpg.add_spacer(width=6)
            dpg.add_text("", tag="net_text")
            dpg.add_spacer(width=24)
            b = dpg.add_button(label="Pause", tag="pause_btn", width=90, callback=self.on_pause)
            dpg.bind_item_theme(b, "theme_primary")
            dpg.add_button(label="Top processes", width=130, callback=lambda: self.set_scope("top"))
            dpg.add_button(label="Export CSV", width=110, callback=self.on_export)
            dpg.add_button(label="Clear history", width=120, callback=self.on_clear)
            dpg.add_button(label="Settings", width=100, callback=lambda: dpg.configure_item("settings_win", show=True))
            if can_elevate() and self.net.reason == "needs administrator rights":
                b = dpg.add_button(label="Restart as admin for network", callback=self.on_elevate)
                dpg.bind_item_theme(b, "theme_warn")
                with dpg.tooltip(b):
                    dpg.add_text(elevation_hint() + "\nCapture is passive (sniff mode) and never touches live traffic.")

    def _build_tiles(self) -> None:
        with dpg.group(horizontal=True, tag="tiles_row"):
            for tile_id, label, metrics in TILES:
                tag = f"tile_{tile_id}"
                with dpg.child_window(tag=tag, width=-1 if tile_id == TILES[-1][0] else 0, height=112,
                                      no_scrollbar=True):
                    with dpg.group(horizontal=True):
                        h = dpg.add_text(label, color=T.SUBTEXT)
                        bind_font(h, "font_small")
                        dpg.add_spacer(width=10)
                        p = dpg.add_text("", tag=f"{tag}_peak", color=T.OVERLAY1)
                        bind_font(p, "font_small")
                    v = dpg.add_text("--", tag=f"{tag}_value")
                    bind_font(v, "font_big")
                    with dpg.plot(tag=f"{tag}_plot", height=34, width=-1, no_title=True, no_menus=True,
                                  no_box_select=True, no_mouse_pos=True, no_inputs=True):
                        dpg.add_plot_axis(dpg.mvXAxis, tag=f"{tag}_x", no_tick_labels=True, no_tick_marks=True,
                                          no_gridlines=True)
                        dpg.add_plot_axis(dpg.mvYAxis, tag=f"{tag}_y", no_tick_labels=True, no_tick_marks=True,
                                          no_gridlines=True)
                    dpg.bind_item_theme(f"{tag}_plot", "theme_sparkline")
                dpg.bind_item_theme(tag, "theme_tile")
        # child windows cannot take click handlers, so hit-test the hovered tile on any click
        with dpg.handler_registry():
            dpg.add_mouse_click_handler(callback=self.on_mouse_click)
        # equal widths: DearPyGui cannot flex, so size tiles from the viewport on each frame
        self._tile_count = len(TILES)

    def _build_table_panel(self) -> None:
        s = self.settings
        with dpg.child_window(width=s.left_panel_width, height=-34, tag="left_panel"):
            with dpg.group(horizontal=True):
                h = dpg.add_text("Processes")
                bind_font(h, "font_heading")
                dpg.add_spacer(width=12)
                dpg.add_input_text(tag="search", hint="filter by name", width=220, callback=self.on_filter)
                dpg.add_checkbox(label="Group by name", default_value=s.group_by_name, callback=self.on_group)
                dpg.add_checkbox(label="Hide idle", default_value=s.hide_idle, callback=self.on_hide_idle)
            dpg.add_text("Click a row to chart it. Right-click a row or a legend entry to end that process. "
                         "Click a column header to sort.", color=T.SUBTEXT)
            dpg.add_spacer(height=2)
            with dpg.table(tag="proc_table", header_row=True, row_background=True, borders_innerH=True,
                           resizable=True, scrollY=True, scrollX=True, height=-1, sortable=True,
                           policy=dpg.mvTable_SizingFixedFit, callback=self.on_sort, freeze_rows=1,
                           freeze_columns=2):
                dpg.add_table_column(label="#", no_sort=True, width_fixed=True, init_width_or_weight=34)
                dpg.add_table_column(label="Process", no_sort=True, width_fixed=True, init_width_or_weight=210)
                dpg.add_table_column(label="PID", no_sort=True, width_fixed=True, init_width_or_weight=64)
                for header, mid, field in TABLE_COLUMNS:
                    tag = f"col_{mid}_{field}"
                    width = 96 if mid == "cpu" and field == "current" else 80
                    dpg.add_table_column(label=header, tag=tag, width_fixed=True, init_width_or_weight=width,
                                         prefer_sort_descending=True,
                                         default_sort=(mid == s.sort_metric and field == s.sort_field))
            dpg.bind_item_theme("proc_table", "theme_table")
        dpg.bind_item_theme("left_panel", "theme_card")
        dpg.bind_item_theme("left_panel", "theme_bar")

    def _build_chart_panel(self) -> None:
        with dpg.child_window(width=-1, height=-34, tag="right_panel"):
            with dpg.group(horizontal=True):
                h = dpg.add_text("Chart", tag="chart_title")
                bind_font(h, "font_heading")
            dpg.add_text("", tag="chart_sub", color=T.SUBTEXT)
            with dpg.group(horizontal=True):
                dpg.add_radio_button(items=[g.label for g in CHART_GROUPS], horizontal=True,
                                     default_value=CHART_GROUP_BY_ID[self.chart_group].label,
                                     callback=self.on_group_radio, tag="group_radio")
                dpg.add_spacer(width=20)
                dpg.add_radio_button(items=["System", "Top processes", "Selected"], horizontal=True,
                                     default_value="Top processes", callback=self.on_scope_radio,
                                     tag="scope_radio")
            # Metrics with different units (GPU % and VRAM) get two stacked plots rather than
            # a second y axis: hiding an axis removes the legend in DearPyGui 2.x, and ten
            # lines on one dual-axis chart are unreadable anyway.
            for suffix in ("", "_b"):
                with dpg.plot(tag=f"plot{suffix}", height=-1, width=-1, no_title=True, show=(suffix == "")):
                    dpg.add_plot_axis(dpg.mvXAxis, tag=f"x_axis{suffix}")
                    dpg.add_plot_axis(dpg.mvYAxis, tag=f"y_axis{suffix}", label="%")
                    # a vertical legend outside the plot on the right, so it never covers data
                    # no_menus: right-click on a legend entry is ours (end process), not ImPlot's menu
                    dpg.add_plot_legend(tag=f"legend{suffix}", location=dpg.mvPlot_Location_NorthWest,
                                        no_menus=True)
                bind_font(f"plot{suffix}", "font_small")     # compact legend and axis text
        dpg.bind_item_theme("right_panel", "theme_card")

    def _build_settings_window(self) -> None:
        s = self.settings
        with dpg.window(label="Settings", tag="settings_win", modal=True, show=False, width=560, height=470,
                        no_resize=True, no_collapse=True, pos=(300, 160)):
            dpg.add_text("Sampling", color=T.BLUE)
            dpg.add_slider_float(label="interval (s)", default_value=s.sample_interval, min_value=0.2,
                                 max_value=5.0, format="%.1f", width=280, callback=self.on_interval)
            dpg.add_slider_int(label="history kept (min)", default_value=s.history_seconds // 60, min_value=1,
                               max_value=720, width=280, callback=self.on_history)
            dpg.add_slider_float(label="ignore processes under (% of a core)", default_value=s.cpu_threshold,
                                 min_value=0.0, max_value=10.0, format="%.1f", width=280,
                                 callback=self._setter("cpu_threshold", float))
            dpg.add_checkbox(label="passive network capture (admin only)", default_value=s.network_capture,
                             enabled=self.net.available, callback=self.on_capture_toggle)
            dpg.add_spacer(height=6)
            dpg.add_text("Table", color=T.BLUE)
            dpg.add_slider_int(label="ranking window (s)", default_value=s.ranking_window, min_value=2,
                               max_value=600, width=280, callback=self._setter("ranking_window", int))
            dpg.add_slider_int(label="rows", default_value=s.table_rows, min_value=5, max_value=200,
                               width=280, callback=self._setter("table_rows", int))
            dpg.add_spacer(height=6)
            dpg.add_text("Chart", color=T.BLUE)
            dpg.add_slider_int(label="chart span (s)", default_value=s.chart_seconds, min_value=10,
                               max_value=3600, width=280, callback=self._setter("chart_seconds", int))
            dpg.add_slider_int(label="top processes charted", default_value=s.chart_lines, min_value=1,
                               max_value=10, width=280, callback=self._setter("chart_lines", int))
            dpg.add_spacer(height=10)
            dpg.add_text(f"Settings are saved to {SETTINGS_FILE.name} on exit.", color=T.SUBTEXT)
            dpg.add_button(label="Close", width=100, callback=lambda: dpg.configure_item("settings_win", show=False))

    # ============================================================ callbacks
    def _setter(self, attr: str, cast):
        def cb(sender, value):
            setattr(self.settings, attr, cast(value))
            self.settings.validate()
            self._invalidate()
        return cb

    def _invalidate(self) -> None:
        self._next_table = self._next_chart = self._next_tiles = 0.0

    def on_interval(self, sender, value) -> None:
        self.settings.sample_interval = float(value)
        self.settings.validate()
        self.store.set_maxlen(self.settings.max_snapshots)

    def on_history(self, sender, minutes) -> None:
        self.settings.history_seconds = int(minutes) * 60
        self.settings.validate()
        self.store.set_maxlen(self.settings.max_snapshots)

    def on_capture_toggle(self, sender, value) -> None:
        self.settings.network_capture = bool(value)
        if value:
            self.net.start()
        else:
            self.net.stop()

    def on_pause(self) -> None:
        self.sampler.set_paused(not self.sampler.paused)
        dpg.configure_item("pause_btn", label="Resume" if self.sampler.paused else "Pause")

    def on_clear(self) -> None:
        self.store.clear()
        self.selection = None
        if self.scope == "selected":
            self.scope = "top"
        self._invalidate()
        self.flash("History and peaks cleared")

    def on_filter(self, sender, value) -> None:
        self.name_filter = value
        self._next_table = 0.0

    def on_group(self, sender, value) -> None:
        self.settings.group_by_name = bool(value)
        self.selection = None
        if self.scope == "selected":
            self.scope = "top"
        self._invalidate()

    def on_hide_idle(self, sender, value) -> None:
        self.settings.hide_idle = bool(value)
        self._next_table = 0.0

    def on_sort(self, sender, specs) -> None:
        if not specs:
            return
        col, direction = specs[0]
        alias = dpg.get_item_alias(col) or ""
        parts = alias.split("_", 1)[1] if alias.startswith("col_") else ""
        if not parts:
            return
        mid, field = parts.rsplit("_", 1)
        self.settings.sort_metric, self.settings.sort_field = mid, field
        self.settings.sort_descending = direction < 0
        self._next_table = 0.0

    def on_row_click(self, sender, app_data, row: EntityRow) -> None:
        if self.selection and self.selection["key"] == row.key:
            self.set_scope("top")
            return
        self.selection = {"key": row.key, "label": row.label, "pids": list(row.pids)}
        self.set_scope("selected")

    def on_mouse_click(self, sender, app_data) -> None:
        button = app_data if isinstance(app_data, int) else dpg.mvMouseButton_Left
        if button == dpg.mvMouseButton_Left:
            for tile_id, _label, _metrics in TILES:
                if dpg.is_item_hovered(f"tile_{tile_id}"):
                    self.on_tile_click(tile_id)
                    return
        elif button == dpg.mvMouseButton_Right:
            for item, row in self._row_items:
                if dpg.does_item_exist(item) and dpg.is_item_hovered(item):
                    self.open_kill_dialog(row.name, row.pids, row.key)
                    return
            target = self._hovered_legend_target()
            if target:
                self.open_kill_dialog(*target)

    def _hovered_legend_target(self):
        """(name, pids, key) for the chart series whose legend entry is under the mouse."""
        for prefix in ("", "_b"):
            for key, cur in self._series[prefix].items():
                if not (dpg.does_item_exist(cur["line"]) and dpg.is_item_hovered(cur["line"])):
                    continue
                if key[0] == "top":
                    for rows in self.top_rows_by.values():
                        for r in rows:
                            if r.key == key[1]:
                                return r.name, list(r.pids), r.key
                elif key[0] == "sel" and self.selection:
                    sel = self.selection
                    name = sel["key"][1] if sel["key"][0] == "name" else sel["label"]
                    return name, list(sel["pids"]), sel["key"]
        return None

    # ------------------------------------------------------------- killing
    def _build_kill_window(self) -> None:
        with dpg.window(tag="kill_win", modal=True, show=False, no_title_bar=True, no_resize=True,
                        no_move=False, autosize=True, min_size=(380, 120)):
            h = dpg.add_text("", tag="kill_title")
            bind_font(h, "font_heading")
            dpg.add_text("", tag="kill_detail", color=T.SUBTEXT)
            dpg.add_checkbox(label="also end child processes", default_value=True, tag="kill_children")
            dpg.add_spacer(height=4)
            with dpg.group(horizontal=True):
                b = dpg.add_button(label="End process", width=140, callback=self.on_kill_confirm)
                dpg.bind_item_theme(b, "theme_danger")
                dpg.add_button(label="Cancel", width=100, callback=lambda: dpg.configure_item("kill_win", show=False))
        self._kill_target: Optional[dict] = None

    def open_kill_dialog(self, name: str, pids: list[int], key) -> None:
        self._kill_target = {"name": name, "pids": list(pids), "key": key}
        n = len(pids)
        dpg.set_value("kill_title", f"End {name}?" if n == 1 else f"End all {n} {name} processes?")
        pids = ", ".join(str(p) for p in pids[:8]) + (" ..." if n > 8 else "")
        dpg.set_value("kill_detail", f"PID {pids}\nTerminated immediately, unsaved work in it is lost.")
        dpg.set_value("kill_children", True)
        try:
            mx, my = dpg.get_mouse_pos(local=False)
            dpg.configure_item("kill_win", pos=(int(mx) - 20, int(my) - 20))
        except Exception:
            pass
        dpg.configure_item("kill_win", show=True)

    def on_kill_confirm(self) -> None:
        target = self._kill_target
        dpg.configure_item("kill_win", show=False)
        if target is None:
            return
        report = kill_processes(target["pids"], include_children=bool(dpg.get_value("kill_children")))
        self.flash(f"{target['name']}: {report.summary}", seconds=8)
        if self.selection and self.selection["key"] == target["key"]:
            self.set_scope("top")
        self._invalidate()

    def on_tile_click(self, tile_id: str) -> None:
        """A tile picks the resource only; the scope (System / Top / Selected) is kept."""
        self.chart_group = tile_id
        self.settings.chart_group = tile_id
        dpg.set_value("group_radio", CHART_GROUP_BY_ID[tile_id].label)
        self._invalidate()

    def on_group_radio(self, sender, label) -> None:
        for g in CHART_GROUPS:
            if g.label == label:
                self.chart_group = g.id
        self.settings.chart_group = self.chart_group
        self._invalidate()

    def on_scope_radio(self, sender, label) -> None:
        scope = {"System": "system", "Top processes": "top", "Selected": "selected"}[label]
        if scope == "selected" and not self.selection:
            dpg.set_value("scope_radio", "Top processes")
            self.flash("Click a process row first")
            return
        self.set_scope(scope)

    def set_scope(self, scope: str) -> None:
        """Switch scope. The last selected process is kept so 'Selected' can return to it."""
        if scope == "selected" and not self.selection:
            scope = "top"
        self.scope = scope
        dpg.set_value("scope_radio", {"system": "System", "top": "Top processes", "selected": "Selected"}[scope])
        self._invalidate()

    def on_elevate(self) -> None:
        self.restart_elevated = True
        dpg.stop_dearpygui()

    def on_export(self) -> None:
        try:
            path = self.export_csv()
            self.flash(f"Exported {path.name}")
        except Exception as e:
            log.exception("export failed")
            self.flash(f"Export failed: {e}")

    def flash(self, message: str, seconds: float = 4.0) -> None:
        self.status_message = message
        self.status_until = time.time() + seconds
        self._next_footer = 0.0

    # ============================================================== export
    def export_csv(self):
        EXPORT_DIR.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        seconds = self.settings.history_seconds
        if self.scope == "selected" and self.selection:
            name = "".join(c if c.isalnum() else "_" for c in self.selection["label"])[:40]
            metrics = [m.id for m in PROCESS_METRIC_BY_ID.values()]
            bundle = self.store.entity_series(self.selection["key"], metrics, seconds, self.selection["pids"])
            headers = [PROCESS_METRIC_BY_ID[m].label for m in metrics]
        else:
            name = "system"
            metrics = list(SYSTEM_METRIC_BY_ID)
            bundle = self.store.system_series(metrics, seconds)
            headers = [SYSTEM_METRIC_BY_ID[m].label for m in metrics]
        path = EXPORT_DIR / f"mimir_{name}_{stamp}.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["epoch", "time", *headers])
            for i, ts in enumerate(bundle.ts):
                w.writerow([f"{ts:.3f}", datetime.fromtimestamp(ts).isoformat(timespec="seconds"),
                            *[f"{bundle.values[m][i]:.3f}" for m in metrics]])
        return path

    # ============================================================== updates
    def update_table(self) -> None:
        s = self.settings
        if dpg.does_item_exist("kill_win") and dpg.is_item_shown("kill_win"):
            return                      # keep rows stable while the confirmation is open
        self._row_items = []
        self.rows = self.store.rankings(
            seconds=s.ranking_window, grouped=s.group_by_name, hide_idle=s.hide_idle,
            name_filter=self.name_filter, sort_metric=s.sort_metric, sort_field=s.sort_field,
            descending=s.sort_descending, limit=s.table_rows)
        for child in dpg.get_item_children("proc_table", 1) or []:
            dpg.delete_item(child)

        sel_key = self.selection["key"] if self.selection else None
        i_cpu = PROCESS_METRIC_INDEX["cpu"]
        top_cpu = max([r.current[i_cpu] for r in self.rows] + [0.01])
        # Rows charted in "Top processes": the leaders for the chart's own metric,
        # independent of how the table happens to be sorted.
        group = CHART_GROUP_BY_ID[self.chart_group]
        self.top_rows_by = {}
        colors = {}
        for mids in self._panels(group.metrics, PROCESS_METRIC_BY_ID):
            mid = mids[0]
            rows = self.store.rankings(
                seconds=s.ranking_window, grouped=s.group_by_name, hide_idle=s.hide_idle,
                name_filter=self.name_filter, sort_metric=mid, sort_field="current",
                descending=True, limit=s.chart_lines, only_alive=True)
            rows = [r for r in rows if r.current[PROCESS_METRIC_INDEX[mid]] > 0]
            self.top_rows_by[mid] = rows
            for r in rows:
                if r.key not in colors:
                    colors[r.key] = None
        self.chart_colors = self._assign_colors(list(colors))
        charted = colors if self.scope == "top" else {}
        if self.scope != "selected":
            sel_key = None
        net_on = self.net.active
        gpu_on = self.gpu.per_process

        for rank, row in enumerate(self.rows, 1):
            selected = row.key == sel_key
            with dpg.table_row(parent="proc_table"):
                item = dpg.add_selectable(label=f"{rank}", span_columns=True, default_value=selected,
                                          callback=self.on_row_click, user_data=row)
                self._row_items.append((item, row))
                if selected:
                    color = T.BLUE
                elif row.key in charted:
                    color = charted[row.key]
                else:
                    color = T.TEXT if row.alive else T.OVERLAY0
                dpg.add_text(row.label, color=color)
                dpg.add_text(str(row.pids[0]) if len(row.pids) == 1 else f"{len(row.pids)} pids", color=T.OVERLAY1)
                for header, mid, field in TABLE_COLUMNS:
                    metric = PROCESS_METRIC_BY_ID[mid]
                    mi = PROCESS_METRIC_INDEX[mid]
                    if field == "peak":
                        p = row.peaks[mi]
                        if mid == "gpu_mem" and not gpu_on:
                            dpg.add_text("-", color=T.OVERLAY0)
                        elif p and p.value > 0:
                            dpg.add_text(format_value(metric, p.value), color=T.SUBTEXT)
                            with dpg.tooltip(dpg.last_item()):
                                dpg.add_text(f"peak at {format_clock(p.ts)}")
                        else:
                            dpg.add_text("-", color=T.OVERLAY0)
                        continue
                    v = row.current[mi]
                    if mid == "cpu":
                        dpg.add_progress_bar(default_value=min(1.0, v / top_cpu), overlay=f"{v:.1f}%", width=-1)
                    elif mid in ("net_down", "net_up") and not net_on:
                        dpg.add_text("-", color=T.OVERLAY0)
                    elif mid in ("gpu", "gpu_mem") and not gpu_on:
                        dpg.add_text("-", color=T.OVERLAY0)
                    else:
                        dim = v <= 0
                        dpg.add_text(format_value(metric, v), color=T.OVERLAY0 if dim else T.TEXT)

    # ---------------------------------------------------------------- chart
    def _assign_colors(self, keys: list) -> dict:
        """Sticky colours: a process keeps its colour while charted and for a grace period
        after it drops out, so lines do not swap colours as the ranking shifts."""
        now = time.time()
        palette = T.SERIES_COLORS
        slots = self._color_slots                      # key -> [index, last_seen]
        current = set(keys)
        for key in keys:
            if key in slots:
                slots[key][1] = now
        used = {v[0] for k, v in slots.items() if k in current}
        for key in keys:
            if key in slots:
                continue
            free = [i for i in range(len(palette)) if i not in used]
            if free:
                idx = free[0]
            else:
                # evict the colour of the entity that has been gone the longest
                gone = sorted((v[1], k) for k, v in slots.items() if k not in current)
                if gone:
                    idx = slots.pop(gone[0][1])[0]
                else:
                    idx = len(slots) % len(palette)
            slots[key] = [idx, now]
            used.add(idx)
        for key in [k for k, v in slots.items() if k not in current and now - v[1] > 120]:
            del slots[key]
        return {key: palette[slots[key][0] % len(palette)] for key in keys}

    @staticmethod
    def _short_label(row: EntityRow) -> str:
        """Legend label: executable name without its extension, plus the instance count."""
        name = row.name
        if name.lower().endswith(".exe"):
            name = name[:-4]
        if len(name) > 18:
            name = name[:17] + "…"
        return f"{name} ({len(row.pids)})" if len(row.pids) > 1 else name

    @staticmethod
    def _panels(metric_ids, by_id) -> list[list[str]]:
        """Split a group's metrics into panels: consecutive metrics of one unit share a plot."""
        panels: list[list[str]] = []
        for mid in metric_ids:
            if panels and by_id[panels[-1][0]].kind == by_id[mid].kind:
                panels[-1].append(mid)
            else:
                panels.append([mid])
        return panels[:2]

    def _chart_panels(self) -> list[dict]:
        """One dict per plot: prefix, x (relative seconds), series, primary metric, peak key."""
        s = self.settings
        group = CHART_GROUP_BY_ID[self.chart_group]
        out = []
        if self.scope == "system":
            for i, mids in enumerate(self._panels(group.system_metrics, SYSTEM_METRIC_BY_ID)):
                b = self.store.system_series(mids, s.chart_seconds)
                metrics = [SYSTEM_METRIC_BY_ID[m] for m in mids]
                series = [(("sys", m.id), m.label, b.values[m.id], m.color, m) for m in metrics]
                out.append({"prefix": "_b" if i else "", "ts": b.ts, "series": series,
                            "primary": metrics[0], "peak": ("sys", metrics[0].id)})
        elif self.scope == "selected" and self.selection:
            for i, mids in enumerate(self._panels(group.metrics, PROCESS_METRIC_BY_ID)):
                b = self.store.entity_series(self.selection["key"], mids, s.chart_seconds, self.selection["pids"])
                metrics = [PROCESS_METRIC_BY_ID[m] for m in mids]
                series = [(("sel", m.id), m.label, b.values[m.id], m.color, m) for m in metrics]
                out.append({"prefix": "_b" if i else "", "ts": b.ts, "series": series,
                            "primary": metrics[0], "peak": ("sel", metrics[0].id)})
        else:
            for i, mids in enumerate(self._panels(group.metrics, PROCESS_METRIC_BY_ID)):
                primary = PROCESS_METRIC_BY_ID[mids[0]]
                rows = self.top_rows_by.get(primary.id, [])
                ts, lists = self.store.multi_entity_series([(r.key, r.pids) for r in rows], primary.id,
                                                           s.chart_seconds)
                series = [(("top", r.key), self._short_label(r), y, self.chart_colors.get(r.key, T.BLUE), primary)
                          for r, y in zip(rows, lists)]
                out.append({"prefix": "_b" if i else "", "ts": ts, "series": series,
                            "primary": primary, "peak": None})
        return out

    def update_chart(self) -> None:
        s = self.settings
        panels = self._chart_panels()
        split = len(panels) > 1
        self._layout_plots(split)
        now = time.time()
        x_max = now - self.t0
        x_min = x_max - s.chart_seconds
        ticks = self._time_ticks(x_min, x_max)
        for panel in panels:
            self._render_panel(panel, x_min, x_max, ticks)
        if not split:
            self._clear_panel("_b")
        self._update_chart_titles(panels)

    def _layout_plots(self, split: bool) -> None:
        if split != self._split:
            self._split = split
            dpg.configure_item("plot_b", show=split)
            self._ticks_key = None          # the newly shown plot needs its ticks
        if split:
            try:
                avail = dpg.get_item_rect_size("right_panel")[1] - 124
            except Exception:
                avail = 500
            height = max(120, int(avail // 2) - 6)
        else:
            height = -1
        if height != self._plot_height:
            dpg.configure_item("plot", height=height)
            self._plot_height = height

    def _clear_panel(self, prefix: str) -> None:
        for cur in self._series[prefix].values():
            if dpg.does_item_exist(cur["line"]):
                dpg.delete_item(cur["line"])
        self._series[prefix].clear()
        if dpg.does_item_exist(f"peak_ann{prefix}"):
            dpg.configure_item(f"peak_ann{prefix}", show=False)

    def _render_panel(self, panel: dict, x_min: float, x_max: float, ticks) -> None:
        p = panel["prefix"]
        primary: Metric = panel["primary"]
        x = [t - self.t0 for t in panel["ts"]]
        wanted = {}
        for key, label, y, color, metric in panel["series"]:
            scale = axis_scale(metric)
            wanted[key] = (label, [v * scale for v in y], color)

        existing = self._series[p]
        # The legend lists series in creation order and DearPyGui's reorder does not change
        # what ImPlot sees, so when the ranking order changes the lines are rebuilt in order.
        kept = [k for k in existing if k in wanted and wanted[k][0] == existing[k]["label"]]
        if kept != [k for k in wanted if k in existing]:
            self._clear_panel(p)
            existing = self._series[p]
        for key in list(existing):
            cur = existing[key]
            w = wanted.get(key)
            if w is None or w[0] != cur["label"]:
                if dpg.does_item_exist(cur["line"]):
                    dpg.delete_item(cur["line"])
                del existing[key]

        fill_ok = self.scope != "top" or len(wanted) == 1
        if list(existing) != list(wanted)[:len(existing)]:
            self._clear_panel(p)
            existing = self._series[p]
        for key, (label, y, color) in wanted.items():
            cur = existing.get(key)
            if cur is None:
                line = dpg.add_line_series(x, y, label=label, parent=f"y_axis{p}", shaded=fill_ok)
                dpg.bind_item_theme(line, series_theme(color))
                existing[key] = {"line": line, "label": label, "shaded": fill_ok}
            else:
                dpg.set_value(cur["line"], [x, y])
                if cur["shaded"] != fill_ok:
                    dpg.configure_item(cur["line"], shaded=fill_ok)
                    cur["shaded"] = fill_ok

        dpg.set_axis_limits(f"x_axis{p}", x_min, x_max)
        if ticks:
            dpg.set_axis_ticks(f"x_axis{p}", ticks)
        ys = [v for (l, y, c) in wanted.values() for v in y]
        floor = 5.0 if primary.kind == "percent" else 1.0
        dpg.set_axis_limits(f"y_axis{p}", 0.0, max(max(ys, default=0.0) * 1.15, floor))
        dpg.configure_item(f"y_axis{p}", label=f"{primary.label}  ({axis_label(primary)})")
        self._update_peak_annotation(p, x, wanted, panel["peak"], primary)

    def _time_ticks(self, x_min: float, x_max: float):
        """Clock-time tick labels for the relative x axis; None when unchanged."""
        span = x_max - x_min
        step = next((st for st in (5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600) if span / st <= 8), 3600)
        first = int((self.t0 + x_min) // step + 1) * step
        key = (step, first)
        if key == self._ticks_key:
            return None
        self._ticks_key = key
        fmt = "%H:%M" if step >= 60 else "%H:%M:%S"
        ticks = []
        t = first
        while t <= self.t0 + x_max:
            ticks.append((datetime.fromtimestamp(t).strftime(fmt), float(t - self.t0)))
            t += step
        return tuple(ticks) or None

    def _update_peak_annotation(self, prefix: str, x, wanted, peak_key, primary: Metric) -> None:
        ann = f"peak_ann{prefix}"
        w = wanted.get(peak_key) if peak_key and x else None
        if not w or max(w[1]) <= 0:
            if dpg.does_item_exist(ann):
                dpg.configure_item(ann, show=False)
            return
        y = w[1]
        i = max(range(len(y)), key=y.__getitem__)
        label = f"peak {format_value(primary, y[i] / axis_scale(primary))} at {format_clock(self.t0 + x[i])}"
        if dpg.does_item_exist(ann):
            dpg.configure_item(ann, default_value=(x[i], y[i]), label=label, show=True)
        else:
            dpg.add_plot_annotation(tag=ann, label=label, default_value=(x[i], y[i]), offset=(12, -18),
                                    color=(*primary.color, 200), clamped=True, parent=f"plot{prefix}")

    def _update_chart_titles(self, panels: list[dict]) -> None:
        s = self.settings
        group = CHART_GROUP_BY_ID[self.chart_group]
        primaries = [pn["primary"] for pn in panels]

        def peak_text(metric: Metric, peak) -> str:
            return f"{metric.label} {format_value(metric, peak.value)} at {format_clock(peak.ts)}" if peak else f"{metric.label} -"

        if self.scope == "system":
            title = f"{group.label}  -  whole system"
            sub = "all-time peak: " + ",  ".join(peak_text(m, self.store.system_peak(m.id)) for m in primaries)
        elif self.scope == "selected" and self.selection:
            peaks = self.store.peaks_for(self.selection["key"])
            n = len(self.selection["pids"])
            title = f"{group.label}  -  {self.selection['label']}"
            sub = (f"{n} process{'es' if n != 1 else ''} summed.  all-time peak: "
                   + ",  ".join(peak_text(m, peaks[PROCESS_METRIC_INDEX[m.id]]) for m in primaries))
        else:
            counts = [len(self.top_rows_by.get(m.id, [])) for m in primaries]
            if len(primaries) > 1:
                title = f"{group.label}  -  top processes"
                sub = (f"top: highest current {primaries[0].label.lower()},  bottom: highest current "
                       f"{primaries[1].label.lower()}")
            else:
                n = counts[0]
                if n:
                    title = f"{primaries[0].label}  -  top {n} process{'es' if n != 1 else ''}"
                elif group.id == "net" and not self.net.active:
                    title = f"{group.label}  -  per-process data {self.net.reason}"
                elif group.id == "gpu" and not self.gpu.per_process:
                    title = f"{group.label}  -  per-process data unavailable ({self.gpu.reason})"
                else:
                    title = f"{primaries[0].label}  -  no process is using it"
                sub = f"highest current {primaries[0].label.lower()}"
            sub += f",  {'grouped by name' if s.group_by_name else 'individual processes'}"
        sub += f"   |   last {s.chart_seconds}s"
        dpg.set_value("chart_title", title)
        dpg.set_value("chart_sub", sub)

    def update_tiles(self) -> None:
        now = time.time()
        span = 60.0
        try:
            vw = dpg.get_viewport_client_width()
        except Exception:
            vw = self.settings.window_width
        tile_w = max(180, int((vw - 28 - 10 * (self._tile_count - 1)) / self._tile_count))
        latest = self.store.latest()
        for tile_id, label, metrics in TILES:
            tag = f"tile_{tile_id}"
            if dpg.get_item_width(tag) != tile_w:
                dpg.configure_item(tag, width=tile_w)
            active = self.chart_group == tile_id
            dpg.bind_item_theme(tag, "theme_tile_active" if active else "theme_tile")
            m0 = SYSTEM_METRIC_BY_ID[metrics[0]]
            if latest is None:
                continue
            if tile_id == "gpu" and not self.gpu.available:
                dpg.set_value(f"{tag}_value", "n/a")
                dpg.set_value(f"{tag}_peak", self.gpu.reason)
                continue
            cur = latest.system.get(metrics[0], 0.0)
            if tile_id in ("net", "disk"):
                a, b = latest.system.get(metrics[0], 0.0), latest.system.get(metrics[1], 0.0)
                value = f"{Fonts.arrow_down} {format_value(m0, a)}   {Fonts.arrow_up} {format_value(m0, b)}"
            elif tile_id == "mem":
                value = f"{format_value(m0, cur)}   {cur / max(1.0, self.sampler.mem_total) * 100:.0f}%"
            elif tile_id == "gpu":
                vram = latest.system.get("gpu_mem", 0.0)
                value = f"{cur:.0f}%   {format_value(SYSTEM_METRIC_BY_ID['gpu_mem'], vram)}"
            else:
                value = format_value(m0, cur)
            dpg.set_value(f"{tag}_value", value)
            peak = self.store.system_peak(metrics[0])
            dpg.set_value(f"{tag}_peak", f"peak {format_value(m0, peak.value)} {format_clock(peak.ts)}" if peak else "")

            bundle = self.store.system_series(list(metrics[:2]), span)
            x = [t - self.t0 for t in bundle.ts]
            ymax = 1.0
            for mid in metrics[:2]:
                m = SYSTEM_METRIC_BY_ID[mid]
                if m.kind != m0.kind:
                    continue
                y = bundle.values[mid]
                ymax = max(ymax, max(y, default=0.0) * 1.1)
                skey = f"{tag}_{mid}"
                line = self._tile_series.get(skey)
                if line is None:
                    line = dpg.add_line_series(x, y, parent=f"{tag}_y", shaded=True)
                    dpg.bind_item_theme(line, series_theme(m.color, fill_alpha=0.18, weight=1.5))
                    self._tile_series[skey] = line
                else:
                    dpg.set_value(line, [x, y])
            dpg.set_axis_limits(f"{tag}_x", now - span - self.t0, now - self.t0)
            if m0.kind == "percent" and tile_id != "gpu":
                ymax = 100.0
            dpg.set_axis_limits(f"{tag}_y", 0.0, ymax)

    def update_header_footer(self) -> None:
        dot = Fonts.dot
        if self.sampler.last_error:
            dpg.set_value("status_text", f"{dot} Sampling error")
            dpg.configure_item("status_text", color=T.RED)
        elif self.sampler.paused:
            dpg.set_value("status_text", f"{dot} Paused")
            dpg.configure_item("status_text", color=T.YELLOW)
        else:
            dpg.set_value("status_text", f"{dot} Sampling every {self.settings.sample_interval:.1f}s")
            dpg.configure_item("status_text", color=T.GREEN)

        if self.net.active:
            txt, color = f"{dot} Network capture on (passive)", T.TEAL
        elif self.net.stats.error:
            txt, color = f"{dot} Network capture failed", T.RED
        elif not self.net.available:
            txt, color = f"{dot} Per-process network: {self.net.reason}", T.YELLOW
        elif not self.settings.network_capture:
            txt, color = f"{dot} Network capture off", T.SUBTEXT
        else:
            txt, color = f"{dot} Network capture starting", T.SUBTEXT
        dpg.set_value("net_text", txt)
        dpg.configure_item("net_text", color=color)

        n = len(self.store)
        parts = [f"{n} samples ({self.store.span_seconds() / 60:.1f} min of {self.settings.history_seconds // 60})",
                 f"sample took {self.sampler.last_sample_ms:.0f} ms"]
        latest = self.store.latest()
        if latest:
            parts.append(f"{len(latest.procs)} processes via {self.sampler.backend}")
        if self.gpu.available:
            parts.append(f"{self.gpu.name} via {self.gpu.source}")
        if self.net.active:
            st = self.net.stats
            parts.append(f"{st.attributed}/{st.packets} packets attributed")
        if self.sampler.last_error:
            parts.append(self.sampler.last_error)
        if self.status_message and time.time() < self.status_until:
            parts.append(self.status_message)
        dpg.set_value("footer_text", "   |   ".join(parts))

    # ================================================================== run
    def run(self, shot_after: Optional[float] = None, shot_file: str = "mimir_shot.png") -> bool:
        """Render loop. Returns True if the user asked to relaunch elevated."""
        self.build()
        self.sampler.start()
        shot_at = time.time() + shot_after if shot_after else None
        hwnd = viewport_handle(WINDOW_TITLE)
        next_alive = time.time() + 1.0
        try:
            while dpg.is_dearpygui_running():
                now = time.time()
                if hwnd and now >= next_alive:
                    # A host that adopted our window (Dashpan) can take it down with itself;
                    # DearPyGui then spins at full CPU on a dead window. Leave instead.
                    if not window_alive(hwnd):
                        log.warning("viewport window vanished; exiting")
                        break
                    next_alive = now + 1.0
                if now >= self._next_table:
                    self.update_table()
                    self._next_table = now + 1.0
                if now >= self._next_chart:
                    self.update_chart()
                    self._next_chart = now + 0.5
                if now >= self._next_tiles:
                    self.update_tiles()
                    self._next_tiles = now + 1.0
                if now >= self._next_footer:
                    self.update_header_footer()
                    self._next_footer = now + 0.5
                if shot_at and now >= shot_at:
                    dpg.output_frame_buffer(shot_file)
                    log.info("saved %s", shot_file)
                    shot_at = None
                    dpg.stop_dearpygui()
                dpg.render_dearpygui_frame()
        finally:
            self._persist_window()
            self.settings.save(SETTINGS_FILE)
            self.sampler.stop()
            dpg.destroy_context()
        if self.restart_elevated:
            return relaunch_elevated()
        return False

    def _persist_window(self) -> None:
        try:
            self.settings.window_width = dpg.get_viewport_width()
            self.settings.window_height = dpg.get_viewport_height()
            w = dpg.get_item_width("left_panel")
            if w and w > 0:
                self.settings.left_panel_width = w
            self.settings.chart_group = self.chart_group
            self.settings.validate()
        except Exception:
            pass
