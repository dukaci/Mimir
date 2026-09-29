#include "theme.h"

#include <implot.h>

#include "log.h"
#include "sys.h"

namespace theme {

Fonts fonts;

void apply() {
    ImGuiStyle& s = ImGui::GetStyle();
    s.WindowPadding = {10, 8};
    s.FramePadding = {8, 4};
    s.ItemSpacing = {8, 6};
    s.ItemInnerSpacing = {6, 4};
    s.CellPadding = {6, 2};
    s.FrameRounding = s.PopupRounding = s.GrabRounding = s.ScrollbarRounding = 6;
    s.ChildRounding = s.WindowRounding = 10;
    s.ScrollbarSize = 12;
    s.GrabMinSize = 14;
    s.ChildBorderSize = 1;
    s.WindowBorderSize = 0;

    auto set = [&](ImGuiCol c, uint32_t v) { s.Colors[c] = vec(v); };
    set(ImGuiCol_WindowBg, MANTLE);
    set(ImGuiCol_ChildBg, BASE);
    set(ImGuiCol_PopupBg, SURFACE0);
    set(ImGuiCol_Border, SURFACE0);
    set(ImGuiCol_Text, TEXT);
    set(ImGuiCol_TextDisabled, OVERLAY0);
    set(ImGuiCol_FrameBg, SURFACE0);
    set(ImGuiCol_FrameBgHovered, SURFACE1);
    set(ImGuiCol_FrameBgActive, SURFACE2);
    set(ImGuiCol_TitleBg, MANTLE);
    set(ImGuiCol_TitleBgActive, BASE);
    set(ImGuiCol_Button, SURFACE1);
    set(ImGuiCol_ButtonHovered, SURFACE2);
    set(ImGuiCol_ButtonActive, OVERLAY0);
    set(ImGuiCol_Header, alpha(BLUE, 70));
    set(ImGuiCol_HeaderHovered, alpha(BLUE, 45));
    set(ImGuiCol_HeaderActive, alpha(BLUE, 110));
    set(ImGuiCol_TableHeaderBg, SURFACE0);
    set(ImGuiCol_TableRowBg, 0);
    set(ImGuiCol_TableRowBgAlt, rgb(255, 255, 255, 6));
    set(ImGuiCol_TableBorderLight, alpha(SURFACE0, 160));
    set(ImGuiCol_TableBorderStrong, SURFACE1);
    set(ImGuiCol_SliderGrab, BLUE);
    set(ImGuiCol_SliderGrabActive, SAPPHIRE);
    set(ImGuiCol_CheckMark, BLUE);
    set(ImGuiCol_ScrollbarBg, 0);
    set(ImGuiCol_ScrollbarGrab, SURFACE1);
    set(ImGuiCol_ScrollbarGrabHovered, SURFACE2);
    set(ImGuiCol_ScrollbarGrabActive, OVERLAY0);
    set(ImGuiCol_Separator, SURFACE0);
    set(ImGuiCol_PlotHistogram, alpha(BLUE, 200));        // progress bar fill
    set(ImGuiCol_PlotHistogramHovered, SAPPHIRE);
    set(ImGuiCol_ResizeGrip, 0);
    set(ImGuiCol_ResizeGripHovered, alpha(BLUE, 120));
    set(ImGuiCol_ResizeGripActive, BLUE);
    set(ImGuiCol_ModalWindowDimBg, rgb(0, 0, 0, 140));

    ImPlotStyle& p = ImPlot::GetStyle();
    auto setp = [&](ImPlotCol c, uint32_t v) { p.Colors[c] = vec(v); };
    setp(ImPlotCol_FrameBg, 0);
    setp(ImPlotCol_PlotBg, MANTLE);
    setp(ImPlotCol_PlotBorder, SURFACE0);
    setp(ImPlotCol_LegendBg, alpha(MANTLE, 235));
    setp(ImPlotCol_LegendBorder, SURFACE1);
    setp(ImPlotCol_LegendText, TEXT);
    setp(ImPlotCol_AxisGrid, alpha(SURFACE1, 110));
    setp(ImPlotCol_AxisText, SUBTEXT);
    setp(ImPlotCol_AxisTick, SURFACE2);
    setp(ImPlotCol_Crosshairs, alpha(SUBTEXT, 120));
    setp(ImPlotCol_InlayText, SUBTEXT);
    p.PlotPadding = {12, 12};
    p.LegendPadding = {6, 4};
    p.LegendInnerPadding = {6, 3};
    p.LegendSpacing = {8, 2};
    p.LabelPadding = {8, 6};
    p.MinorAlpha = 0.15f;
    p.PlotBorderSize = 1;
    p.PlotMinSize = {10, 10};                              // tile sparklines are only ~30 px tall
    p.UseLocalTime = p.Use24HourClock = true;

    // ImGui 1.92 rasterizes glyphs on demand, at any size: one file per weight is enough.
    ImGuiIO& io = ImGui::GetIO();
    io.IniFilename = nullptr;                                // no imgui.ini: settings.json holds the state
    auto [regular, bold] = ui_fonts();
    ImFontConfig cfg;
    if (!regular.empty() && (fonts.regular = io.Fonts->AddFontFromFileTTF(regular.c_str(), UI, &cfg))) {
        fonts.bold = io.Fonts->AddFontFromFileTTF(bold.c_str(), UI, &cfg);
        if (!fonts.bold) fonts.bold = fonts.regular;
        fonts.symbols = fonts.regular->IsGlyphInFont(0x2193) && fonts.regular->IsGlyphInFont(0x25CF);
    } else {
        log_info("no UI font found; using Dear ImGui's built-in font");
        fonts.regular = fonts.bold = io.Fonts->AddFontDefault();
    }
    s.FontSizeBase = UI;
}

}  // namespace theme
