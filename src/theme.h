// Colours (Catppuccin Mocha), style and fonts.
#pragma once

#include <imgui.h>

#include "metrics.h"

namespace theme {

constexpr uint32_t BASE = rgb(30, 30, 46), MANTLE = rgb(24, 24, 37), CRUST = rgb(17, 17, 27);
constexpr uint32_t SURFACE0 = rgb(49, 50, 68), SURFACE1 = rgb(69, 71, 90), SURFACE2 = rgb(88, 91, 112);
constexpr uint32_t OVERLAY0 = rgb(108, 112, 134), OVERLAY1 = rgb(127, 132, 156);
constexpr uint32_t TEXT = rgb(205, 214, 244), SUBTEXT = rgb(166, 173, 200);
constexpr uint32_t BLUE = rgb(137, 180, 250), SAPPHIRE = rgb(116, 199, 236), SKY = rgb(137, 220, 235);
constexpr uint32_t TEAL = rgb(148, 226, 213), GREEN = rgb(166, 227, 161), YELLOW = rgb(249, 226, 175);
constexpr uint32_t RED = rgb(243, 139, 168);

// Series palette: hues spread around the wheel so neighbouring lines never look alike
inline constexpr uint32_t kSeries[] = {rgb(137, 180, 250), rgb(250, 179, 135), rgb(166, 227, 161), rgb(243, 139, 168),
                                       rgb(203, 166, 247), rgb(249, 226, 175), rgb(148, 226, 213), rgb(225, 225, 235),
                                       rgb(232, 140, 220), rgb(205, 170, 120)};
constexpr int kSeriesCount = int(sizeof kSeries / sizeof kSeries[0]);

// Font sizes in pixels
constexpr float SMALL = 14, TABLE = 15, UI = 17, HEADING = 18, TITLE = 26, BIG = 30;

struct Fonts {
    ImFont* regular = nullptr;
    ImFont* bold = nullptr;
    bool symbols = false;           // the font has the arrows, dots and triangle the UI uses
};
extern Fonts fonts;

void apply();                       // style, colours and fonts; call once after ImGui::CreateContext

inline const char* dot() { return fonts.symbols ? "●" : "*"; }
inline const char* arrow_down() { return fonts.symbols ? "↓" : "v"; }
inline const char* arrow_up() { return fonts.symbols ? "↑" : "^"; }
inline const char* peak() { return fonts.symbols ? "▲" : "max"; }

inline ImVec4 vec(uint32_t c) { return ImGui::ColorConvertU32ToFloat4(c); }
inline uint32_t alpha(uint32_t c, uint8_t a) { return (c & 0x00FFFFFF) | uint32_t(a) << 24; }

}  // namespace theme
