"""Colours, fonts and DearPyGui themes for Mimir (Catppuccin Mocha)."""

from __future__ import annotations

import logging
import os

import dearpygui.dearpygui as dpg

from ..platform import font_candidates

log = logging.getLogger(__name__)

BASE = (30, 30, 46)
MANTLE = (24, 24, 37)
CRUST = (17, 17, 27)
SURFACE0 = (49, 50, 68)
SURFACE1 = (69, 71, 90)
SURFACE2 = (88, 91, 112)
OVERLAY0 = (108, 112, 134)
OVERLAY1 = (127, 132, 156)
TEXT = (205, 214, 244)
SUBTEXT = (166, 173, 200)
BLUE = (137, 180, 250)
SAPPHIRE = (116, 199, 236)
SKY = (137, 220, 235)
TEAL = (148, 226, 213)
GREEN = (166, 227, 161)
YELLOW = (249, 226, 175)
PEACH = (250, 179, 135)
RED = (243, 139, 168)
PINK = (245, 194, 231)
MAUVE = (203, 166, 247)
LAVENDER = (180, 190, 254)

# Series palette: hues spread around the wheel so neighbouring lines never look alike
SERIES_COLORS = [
    (137, 180, 250),   # blue
    (250, 179, 135),   # orange
    (166, 227, 161),   # green
    (243, 139, 168),   # red
    (203, 166, 247),   # purple
    (249, 226, 175),   # yellow
    (148, 226, 213),   # teal
    (225, 225, 235),   # white
    (232, 140, 220),   # magenta
    (205, 170, 120),   # tan
]


class Fonts:
    loaded = False
    dot = "*"
    arrow_down = "v"
    arrow_up = "^"


def setup_fonts() -> None:
    for regular, semibold in font_candidates():
        if not os.path.exists(regular):
            continue
        try:
            with dpg.font_registry():
                dpg.add_font(regular, 17, tag="font_ui")
                dpg.add_font(regular, 14, tag="font_small")
                dpg.add_font(regular, 30, tag="font_big")
                dpg.add_font(semibold if os.path.exists(semibold) else regular, 26, tag="font_title")
                dpg.add_font(semibold if os.path.exists(semibold) else regular, 18, tag="font_heading")
            dpg.bind_font("font_ui")
            Fonts.loaded = True
            Fonts.dot = "●"
            Fonts.arrow_down = "↓"
            Fonts.arrow_up = "↑"
            return
        except Exception as e:      # bad font file: keep the built-in one
            log.warning("could not load font %s: %s", regular, e)
    log.info("using DearPyGui's built-in font")


def bind_font(item, name: str) -> None:
    if Fonts.loaded:
        dpg.bind_item_font(item, name)


def _plot_theme_component():
    colp = lambda c, v: dpg.add_theme_color(c, v, category=dpg.mvThemeCat_Plots)
    styp = lambda s, *v: dpg.add_theme_style(s, *v, category=dpg.mvThemeCat_Plots)
    colp(dpg.mvPlotCol_FrameBg, (0, 0, 0, 0))
    colp(dpg.mvPlotCol_PlotBg, MANTLE)
    colp(dpg.mvPlotCol_PlotBorder, SURFACE0)
    colp(dpg.mvPlotCol_LegendBg, (*MANTLE, 235))
    colp(dpg.mvPlotCol_LegendBorder, SURFACE1)
    colp(dpg.mvPlotCol_LegendText, TEXT)
    colp(dpg.mvPlotCol_AxisGrid, (*SURFACE1, 110))
    colp(dpg.mvPlotCol_AxisText, SUBTEXT)
    colp(dpg.mvPlotCol_AxisTick, SURFACE2)
    colp(dpg.mvPlotCol_Crosshairs, (*SUBTEXT, 120))
    colp(dpg.mvPlotCol_InlayText, SUBTEXT)
    styp(dpg.mvPlotStyleVar_PlotPadding, 12, 12)
    styp(dpg.mvPlotStyleVar_LegendPadding, 6, 4)
    styp(dpg.mvPlotStyleVar_LegendInnerPadding, 6, 3)
    styp(dpg.mvPlotStyleVar_LegendSpacing, 8, 2)
    styp(dpg.mvPlotStyleVar_LabelPadding, 8, 6)
    styp(dpg.mvPlotStyleVar_MinorAlpha, 0.15)
    styp(dpg.mvPlotStyleVar_PlotBorderSize, 1)


def setup_themes() -> None:
    with dpg.theme() as global_theme:
        with dpg.theme_component(dpg.mvAll):
            col = dpg.add_theme_color
            sty = dpg.add_theme_style
            col(dpg.mvThemeCol_WindowBg, MANTLE)
            col(dpg.mvThemeCol_ChildBg, BASE)
            col(dpg.mvThemeCol_PopupBg, SURFACE0)
            col(dpg.mvThemeCol_Border, SURFACE0)
            col(dpg.mvThemeCol_Text, TEXT)
            col(dpg.mvThemeCol_TextDisabled, OVERLAY0)
            col(dpg.mvThemeCol_FrameBg, SURFACE0)
            col(dpg.mvThemeCol_FrameBgHovered, SURFACE1)
            col(dpg.mvThemeCol_FrameBgActive, SURFACE2)
            col(dpg.mvThemeCol_TitleBg, MANTLE)
            col(dpg.mvThemeCol_TitleBgActive, BASE)
            col(dpg.mvThemeCol_Button, SURFACE1)
            col(dpg.mvThemeCol_ButtonHovered, SURFACE2)
            col(dpg.mvThemeCol_ButtonActive, OVERLAY0)
            col(dpg.mvThemeCol_Header, (*BLUE, 70))
            col(dpg.mvThemeCol_HeaderHovered, (*BLUE, 45))
            col(dpg.mvThemeCol_HeaderActive, (*BLUE, 110))
            col(dpg.mvThemeCol_TableHeaderBg, SURFACE0)
            col(dpg.mvThemeCol_TableRowBg, (0, 0, 0, 0))
            col(dpg.mvThemeCol_TableRowBgAlt, (255, 255, 255, 6))
            col(dpg.mvThemeCol_TableBorderLight, (*SURFACE0, 160))
            col(dpg.mvThemeCol_TableBorderStrong, SURFACE1)
            col(dpg.mvThemeCol_SliderGrab, BLUE)
            col(dpg.mvThemeCol_SliderGrabActive, SAPPHIRE)
            col(dpg.mvThemeCol_CheckMark, BLUE)
            col(dpg.mvThemeCol_ScrollbarBg, (0, 0, 0, 0))
            col(dpg.mvThemeCol_ScrollbarGrab, SURFACE1)
            col(dpg.mvThemeCol_ScrollbarGrabHovered, SURFACE2)
            col(dpg.mvThemeCol_ScrollbarGrabActive, OVERLAY0)
            col(dpg.mvThemeCol_Separator, SURFACE0)
            col(dpg.mvThemeCol_PlotHistogram, (*BLUE, 200))
            col(dpg.mvThemeCol_PlotHistogramHovered, SAPPHIRE)
            col(dpg.mvThemeCol_ResizeGrip, (0, 0, 0, 0))
            col(dpg.mvThemeCol_ResizeGripHovered, (*BLUE, 120))
            col(dpg.mvThemeCol_ResizeGripActive, BLUE)
            col(dpg.mvThemeCol_ModalWindowDimBg, (0, 0, 0, 140))
            sty(dpg.mvStyleVar_WindowPadding, 14, 12)
            sty(dpg.mvStyleVar_FramePadding, 10, 6)
            sty(dpg.mvStyleVar_ItemSpacing, 10, 8)
            sty(dpg.mvStyleVar_ItemInnerSpacing, 8, 6)
            sty(dpg.mvStyleVar_CellPadding, 8, 5)
            sty(dpg.mvStyleVar_FrameRounding, 6)
            sty(dpg.mvStyleVar_ChildRounding, 10)
            sty(dpg.mvStyleVar_WindowRounding, 10)
            sty(dpg.mvStyleVar_PopupRounding, 6)
            sty(dpg.mvStyleVar_GrabRounding, 6)
            sty(dpg.mvStyleVar_ScrollbarRounding, 6)
            sty(dpg.mvStyleVar_ScrollbarSize, 12)
            sty(dpg.mvStyleVar_GrabMinSize, 14)
            sty(dpg.mvStyleVar_ChildBorderSize, 1)
            sty(dpg.mvStyleVar_WindowBorderSize, 0)
        with dpg.theme_component(dpg.mvPlot):
            _plot_theme_component()
    dpg.bind_theme(global_theme)

    with dpg.theme(tag="theme_primary"):
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, BLUE)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, SAPPHIRE)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, SKY)
            dpg.add_theme_color(dpg.mvThemeCol_Text, CRUST)
    with dpg.theme(tag="theme_danger"):
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, RED)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (250, 160, 185))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (255, 180, 200))
            dpg.add_theme_color(dpg.mvThemeCol_Text, CRUST)
    with dpg.theme(tag="theme_warn"):
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (*YELLOW, 40))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (*YELLOW, 80))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (*YELLOW, 120))
            dpg.add_theme_color(dpg.mvThemeCol_Text, YELLOW)
            dpg.add_theme_color(dpg.mvThemeCol_Border, (*YELLOW, 120))
            dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 1)
    with dpg.theme(tag="theme_card"):
        with dpg.theme_component(dpg.mvChildWindow):
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 16, 14)
    with dpg.theme(tag="theme_tile"):
        with dpg.theme_component(dpg.mvChildWindow):
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 14, 10)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 6, 2)
    with dpg.theme(tag="theme_tile_active"):
        with dpg.theme_component(dpg.mvChildWindow):
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 14, 10)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 6, 2)
            dpg.add_theme_color(dpg.mvThemeCol_Border, BLUE)
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (36, 37, 56))
    with dpg.theme(tag="theme_sparkline"):
        with dpg.theme_component(dpg.mvPlot):
            dpg.add_theme_color(dpg.mvPlotCol_PlotBg, (0, 0, 0, 0), category=dpg.mvThemeCat_Plots)
            dpg.add_theme_color(dpg.mvPlotCol_PlotBorder, (0, 0, 0, 0), category=dpg.mvThemeCat_Plots)
            dpg.add_theme_color(dpg.mvPlotCol_FrameBg, (0, 0, 0, 0), category=dpg.mvThemeCat_Plots)
            dpg.add_theme_style(dpg.mvPlotStyleVar_PlotPadding, 0, 0, category=dpg.mvThemeCat_Plots)
            dpg.add_theme_style(dpg.mvPlotStyleVar_PlotBorderSize, 0, category=dpg.mvThemeCat_Plots)
    with dpg.theme(tag="theme_bar"):
        with dpg.theme_component(dpg.mvProgressBar):
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (*SURFACE0, 140))
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 6, 2)
    with dpg.theme(tag="theme_table"):
        with dpg.theme_component(dpg.mvTable):
            dpg.add_theme_style(dpg.mvStyleVar_CellPadding, 8, 5)


_series_themes: dict[tuple, int] = {}


def series_theme(color: tuple, fill_alpha: float = 0.12, weight: float = 2.0):
    key = (color, fill_alpha, weight)
    theme = _series_themes.get(key)
    if theme is None:
        with dpg.theme() as theme:
            with dpg.theme_component(dpg.mvLineSeries):
                dpg.add_theme_color(dpg.mvPlotCol_Line, color, category=dpg.mvThemeCat_Plots)
                dpg.add_theme_color(dpg.mvPlotCol_Fill, color, category=dpg.mvThemeCat_Plots)
                dpg.add_theme_style(dpg.mvPlotStyleVar_LineWeight, weight, category=dpg.mvThemeCat_Plots)
                dpg.add_theme_style(dpg.mvPlotStyleVar_FillAlpha, fill_alpha, category=dpg.mvThemeCat_Plots)
        _series_themes[key] = theme
    return theme
