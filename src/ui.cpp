#include "ui.h"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <filesystem>
#include <fstream>

#include <GLFW/glfw3.h>
#include <imgui.h>
#include <imgui_impl_glfw.h>
#include <imgui_impl_opengl3.h>
#include <implot.h>

#ifdef _WIN32
#define GLFW_EXPOSE_NATIVE_WIN32
#include <GLFW/glfw3native.h>
#endif

#define STB_IMAGE_IMPLEMENTATION
#define STBI_ONLY_PNG
#include <stb_image.h>
#define STB_IMAGE_WRITE_IMPLEMENTATION
#include <stb_image_write.h>

#include "log.h"
#include "sys.h"
#include "theme.h"

namespace {

constexpr float NAME_MAX_WIDTH = 240;       // px cap on the Process column; longer names end in "..."
constexpr float BAR_ROOM = 24;              // px of bar around the CPU value, so the bar still reads as a gauge
constexpr double HOT_SECONDS = 1.0;         // after input, frames keep coming for this long (hover, tooltips)

struct Column {
    const char* header;
    Metric metric;
    Field field;
};
// Number columns after "#", "Process" and "PID"
constexpr Column kColumns[] = {
    {"CPU", CPU, Field::Current},    {"CPU", CPU, Field::Peak},       {"Memory", MEM, Field::Current},
    {"Mem", MEM, Field::Peak},       {"Disk R", DISK_R, Field::Current}, {"Disk W", DISK_W, Field::Current},
    {"Down", NET_D, Field::Current}, {"Up", NET_U, Field::Current},   {"GPU", GPU, Field::Current},
    {"VRAM", VRAM, Field::Current},  {"VRAM", VRAM, Field::Peak},
};
constexpr int kColumnCount = int(sizeof kColumns / sizeof kColumns[0]);

Metric metric_by_id(const std::string& id) {
    for (int i = 0; i < kProcMetrics; ++i)
        if (id == kMetrics[i].id) return Metric(i);
    return CPU;
}

Field field_by_name(const std::string& f) {
    return f == "peak" ? Field::Peak : f == "average" ? Field::Average : f == "maximum" ? Field::Maximum : Field::Current;
}

const char* field_name(Field f) {
    return f == Field::Peak ? "peak" : f == Field::Average ? "average" : f == Field::Maximum ? "maximum" : "current";
}

std::string header_of(const Column& c) {
    return c.field == Field::Peak ? std::string(c.header) + " " + theme::peak() : c.header;
}

// The plots of a group: consecutive metrics of one kind share a plot
std::vector<std::vector<Metric>> panels_of(const Group& g) {
    std::vector<std::vector<Metric>> out;
    for (int i = 0; i < g.count; ++i) {
        Metric m = g.metrics[i];
        if (!out.empty() && kMetrics[out.back()[0]].kind == kMetrics[m].kind)
            out.back().push_back(m);
        else
            out.push_back({m});
    }
    return out;
}

std::string short_label(const std::string& name, size_t count) {
    std::string n = name;
    if (n.size() > 4 && (n.ends_with(".exe") || n.ends_with(".EXE"))) n.resize(n.size() - 4);
    if (n.size() > 18) n = n.substr(0, 17) + "...";
    return count > 1 ? n + " (" + std::to_string(count) + ")" : n;
}

struct Font {
    Font(ImFont* f, float size) { ImGui::PushFont(f, size); }
    ~Font() { ImGui::PopFont(); }
};

struct Color {
    explicit Color(uint32_t c) { ImGui::PushStyleColor(ImGuiCol_Text, c); }
    ~Color() { ImGui::PopStyleColor(); }
};

void text(uint32_t color, const std::string& s) {
    Color c(color);
    ImGui::TextUnformatted(s.c_str());
}

void text_right(uint32_t color, const std::string& s) {         // right-aligned in a table cell
    float w = ImGui::CalcTextSize(s.c_str()).x, avail = ImGui::GetContentRegionAvail().x;
    if (avail > w) ImGui::SetCursorPosX(ImGui::GetCursorPosX() + avail - w);
    text(color, s);
}

bool button(const char* label, uint32_t bg, uint32_t fg, float width = 0) {
    ImGui::PushStyleColor(ImGuiCol_Button, bg);
    ImGui::PushStyleColor(ImGuiCol_ButtonHovered, theme::alpha(bg, 200));
    ImGui::PushStyleColor(ImGuiCol_Text, fg);
    bool clicked = ImGui::Button(label, {width, 0});
    ImGui::PopStyleColor(3);
    return clicked;
}

double g_hot_until = 0;                     // written by GLFW input callbacks on the UI thread

void mark_input() { g_hot_until = wall_now() + HOT_SECONDS; }

}  // namespace

App::App(Settings& settings, History& history, Sampler& sampler, Capture& net, Gpu& gpu, int argc, char** argv)
    : settings_(settings), history_(history), sampler_(sampler), net_(net), gpu_(gpu), argc_(argc), argv_(argv),
      left_width_(float(settings.left_panel_width)) {
    for (int i = 0; i < kGroupCount; ++i)
        if (settings.chart_group == kGroups[i].id) group_ = i;
}

void App::on_tick() {
    ticked_ = true;
    glfwPostEmptyEvent();
}

// ================================================================== model

double App::chart_span() const {
    return scope_ == Scope::Selected && selection_ ? settings_.selected_chart_seconds : settings_.chart_seconds;
}

App::Target App::target_of(const EntityRow& r) const {
    std::string name = history_.name(r.name);
    std::string label = r.pids.size() > 1 ? name + " (" + std::to_string(r.pids.size()) + ")" : name;
    return {r.key, name, label, r.pids};
}

// Sticky colours: an entity keeps its colour while charted and for two minutes after,
// so lines do not swap colours as the ranking shifts.
std::unordered_map<uint64_t, uint32_t> App::assign_colors(const std::vector<uint64_t>& keys, double now) {
    std::unordered_map<uint64_t, uint32_t> out;
    std::vector<bool> used(theme::kSeriesCount);
    for (uint64_t k : keys)
        if (auto it = color_slots_.find(k); it != color_slots_.end()) {
            it->second.second = now;
            used[it->second.first] = true;
        }
    for (uint64_t k : keys) {
        if (color_slots_.count(k)) continue;
        int idx = int(std::find(used.begin(), used.end(), false) - used.begin());
        if (idx == theme::kSeriesCount) {           // all taken: reuse the colour gone the longest
            auto oldest = color_slots_.end();
            for (auto it = color_slots_.begin(); it != color_slots_.end(); ++it)
                if (std::find(keys.begin(), keys.end(), it->first) == keys.end() &&
                    (oldest == color_slots_.end() || it->second.second < oldest->second.second))
                    oldest = it;
            idx = oldest != color_slots_.end() ? oldest->second.first : int(color_slots_.size() % theme::kSeriesCount);
            if (oldest != color_slots_.end()) color_slots_.erase(oldest);
        }
        color_slots_[k] = {idx, now};
        used[idx] = true;
    }
    for (auto it = color_slots_.begin(); it != color_slots_.end();)
        it = now - it->second.second > 120 ? color_slots_.erase(it) : std::next(it);
    for (uint64_t k : keys) out[k] = theme::kSeries[color_slots_[k].first];
    return out;
}

void App::rebuild(double now) {
    const Settings& s = settings_;
    now_ = now;
    auto all = history_.aggregate(s.ranking_window, s.group_by_name, s.hide_idle, filter_, s.cpu_smoothing, now);
    auto rows = rank(all, metric_by_id(s.sort_metric), field_by_name(s.sort_field), s.sort_descending, size_t(s.table_rows));

    // The rows charted in "Top processes": the leaders of each plot's own metric
    auto panels = panels_of(kGroups[group_]);
    std::vector<uint64_t> keys;
    for (size_t i = 0; i < 2; ++i) {
        top_[i].clear();
        if (i >= panels.size()) continue;
        Metric m = panels[i][0];
        for (auto& r : rank(all, m, Field::Current, true, size_t(s.chart_lines), true))
            if (r.cur[m] > 0) top_[i].push_back(r);
        for (auto& r : top_[i])
            if (std::find(keys.begin(), keys.end(), r.key) == keys.end()) keys.push_back(r.key);
    }
    auto colors = assign_colors(keys, now);
    build_table(rows, scope_ == Scope::Top ? colors : decltype(colors){});
    build_chart(now, colors);
    build_tiles(now);

    char buf[256];
    std::snprintf(buf, sizeof buf, "%zu samples (%.1f min of %d)   |   sample took %.0f ms", history_.size(),
                  history_.span() / 60, s.history_seconds / 60, sampler_.last_ms.load());
    footer_ = buf;
    if (auto latest = history_.latest()) {
#ifdef _WIN32
        footer_ += "   |   " + std::to_string(latest->procs) + " processes via kernel snapshot";
#else
        footer_ += "   |   " + std::to_string(latest->procs) + " processes via /proc";
#endif
    }
    if (gpu_.available()) footer_ += "   |   " + gpu_.name() + " via " + gpu_.source();
    if (net_.active()) {
#ifdef _WIN32
        footer_ += "   |   " + std::to_string(net_.attributed()) + " network events";
#else
        footer_ += "   |   " + std::to_string(net_.attributed()) + "/" + std::to_string(net_.seen()) + " packets attributed";
#endif
    }
    if (auto e = sampler_.last_error(); !e.empty()) footer_ += "   |   " + e;
    if (now < status_until_) footer_ += "   |   " + status_;
}

void App::build_table(const std::vector<EntityRow>& rows, const std::unordered_map<uint64_t, uint32_t>& charted) {
    bool net_on = net_.active(), gpu_on = gpu_.per_process();
    float top_cpu = 0.01f;
    for (auto& r : rows) top_cpu = std::max(top_cpu, r.cur[CPU]);
    std::optional<uint64_t> sel;
    if (scope_ == Scope::Selected && selection_) sel = selection_->key;

    Font f(theme::fonts.regular, theme::TABLE);
    table_.clear();
    bar_width_ = ImGui::CalcTextSize("100.0%").x + BAR_ROOM;
    for (auto& r : rows) {
        TableLine line{target_of(r), {}, theme::TEXT, {}};
        const std::string& label = line.target.label;
        line.shown = label;
        if (ImGui::CalcTextSize(label.c_str()).x > NAME_MAX_WIDTH) {      // cut the name, keep the count
            std::string count = r.pids.size() > 1 ? " (" + std::to_string(r.pids.size()) + ")" : "";
            std::string name = line.target.name;
            while (!name.empty() && ImGui::CalcTextSize((name + "..." + count).c_str()).x > NAME_MAX_WIDTH) name.pop_back();
            line.shown = name + "..." + count;
        }
        line.color = sel == r.key ? theme::BLUE : charted.count(r.key) ? charted.at(r.key) : r.alive ? theme::TEXT : theme::OVERLAY0;
        line.cells.push_back({r.pids.size() == 1 ? std::to_string(r.pids[0]) : std::to_string(r.pids.size()) + " pids",
                              {}, theme::OVERLAY1});
        for (const Column& c : kColumns) {
            Kind kind = kMetrics[c.metric].kind;
            if (c.field == Field::Peak) {
                const Peak& p = r.peaks[c.metric];
                if ((c.metric == VRAM && !gpu_on) || p.ts == 0 || p.v <= 0)
                    line.cells.push_back({"-", {}, theme::OVERLAY0});
                else
                    line.cells.push_back({format_value(kind, p.v), "peak at " + format_clock(p.ts), theme::SUBTEXT});
                continue;
            }
            float v = r.cur[c.metric];
            if (c.metric == CPU) {
                char buf[16];
                std::snprintf(buf, sizeof buf, "%.1f%%", v);
                line.cells.push_back({buf, {}, theme::TEXT, std::min(1.0f, v / top_cpu)});
            } else if (((c.metric == NET_D || c.metric == NET_U) && !net_on) || ((c.metric == GPU || c.metric == VRAM) && !gpu_on)) {
                line.cells.push_back({"-", {}, theme::OVERLAY0});
            } else {
                line.cells.push_back({format_value(kind, v), {}, v <= 0 ? theme::OVERLAY0 : theme::TEXT});
            }
        }
        table_.push_back(std::move(line));
    }
}

void App::build_chart(double now, const std::unordered_map<uint64_t, uint32_t>& colors) {
    const Group& g = kGroups[group_];
    auto panels = panels_of(g);
    double span = chart_span();
    panels_.clear();
    for (size_t i = 0; i < panels.size() && i < 2; ++i) {
        const std::vector<Metric>& mids = panels[i];
        Panel p{mids[0], {}, {}, 1, -1};
        std::vector<double> ys[2];
        auto add = [&](std::string label, std::optional<Target> t, uint32_t color, bool shaded, std::vector<double> y) {
            p.lines.push_back({label + "##" + std::to_string(p.lines.size()), std::move(t), color, shaded, std::move(y)});
        };
        if (scope_ == Scope::System || (scope_ == Scope::Selected && selection_)) {
            p.ts = scope_ == Scope::System
                       ? history_.system_series(mids.data(), int(mids.size()), span, now, ys)
                       : history_.entity_series(selection_->key, selection_->pids, mids.data(), int(mids.size()), span, now, ys);
            for (size_t j = 0; j < mids.size(); ++j)
                add(kMetrics[mids[j]].label, scope_ == Scope::Selected ? selection_ : std::nullopt, kMetrics[mids[j]].color,
                    true, std::move(ys[j]));
            p.peak_line = 0;
        } else {
            std::vector<const EntityRow*> ents;
            for (auto& r : top_[i]) ents.push_back(&r);
            std::vector<std::vector<double>> out;
            p.ts = history_.multi_series(ents, p.primary, span, now, out);
            for (size_t j = 0; j < ents.size(); ++j)
                add(short_label(history_.name(ents[j]->name), ents[j]->pids.size()), target_of(*ents[j]),
                    colors.at(ents[j]->key), ents.size() == 1, std::move(out[j]));
        }
        double scale = axis_scale(kMetrics[p.primary].kind), top = 0;
        for (Line& l : p.lines)
            for (double& v : l.y) top = std::max(top, v *= scale);
        p.ymax = std::max(top * 1.15, kMetrics[p.primary].kind == Kind::Percent ? 5.0 : 1.0);
        if (p.peak_line >= 0 && (p.lines.empty() || top <= 0)) p.peak_line = -1;
        panels_.push_back(std::move(p));
    }

    // Wall-clock ticks, 8 at most, on whole multiples of a readable step
    ticks_.clear();
    tick_labels_.clear();
    int step = 3600;
    for (int st : {5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600})
        if (span / st <= 8) {
            step = st;
            break;
        }
    for (double t = std::floor((now - span) / step + 1) * step; t <= now; t += step) {
        ticks_.push_back(t);
        tick_labels_.push_back(format_clock(t, step >= 60 ? "%H:%M" : "%H:%M:%S"));
    }

    // Titles
    auto peak_text = [](Metric m, const Peak& p) {
        return std::string(kMetrics[m].label) + " " +
               (p.ts ? format_value(kMetrics[m].kind, p.v) + " at " + format_clock(p.ts) : "-");
    };
    std::string mode = settings_.group_by_name ? "grouped by name" : "individual processes";
    if (scope_ == Scope::System) {
        chart_title_ = std::string(g.label) + "  -  whole system";
        chart_sub_ = "all-time peak: ";
        for (size_t i = 0; i < panels_.size(); ++i)
            chart_sub_ += (i ? ",  " : "") + peak_text(panels_[i].primary, history_.system_peak(panels_[i].primary));
    } else if (scope_ == Scope::Selected && selection_) {
        Peaks peaks = history_.peaks(selection_->key);
        size_t n = selection_->pids.size();
        chart_title_ = std::string(g.label) + "  -  " + selection_->label;
        chart_sub_ = std::to_string(n) + (n == 1 ? " process" : " processes") + " summed.  all-time peak: ";
        for (size_t i = 0; i < panels_.size(); ++i)
            chart_sub_ += (i ? ",  " : "") + peak_text(panels_[i].primary, peaks[panels_[i].primary]);
    } else {
        std::string first = kMetrics[panels_[0].primary].label;
        std::string lower = first;
        for (char& c : lower) c = char(std::tolower(static_cast<unsigned char>(c)));
        if (panels_.size() > 1) {
            std::string second = kMetrics[panels_[1].primary].label;
            for (char& c : second) c = char(std::tolower(static_cast<unsigned char>(c)));
            chart_title_ = std::string(g.label) + "  -  top processes";
            chart_sub_ = "top: highest current " + lower + ",  bottom: highest current " + second;
        } else {
            size_t n = top_[0].size();
            if (n)
                chart_title_ = first + "  -  top " + std::to_string(n) + (n == 1 ? " process" : " processes");
            else if (std::string(g.id) == "net" && !net_.active())
                chart_title_ = std::string(g.label) + "  -  per-process data " + (net_.available() ? "off" : net_.reason());
            else if (std::string(g.id) == "gpu" && !gpu_.per_process())
                chart_title_ = std::string(g.label) + "  -  per-process data unavailable (" + gpu_.reason() + ")";
            else
                chart_title_ = first + "  -  no process is using it";
            chart_sub_ = "highest current " + lower;
        }
        chart_sub_ += ",  " + mode;
    }
    chart_sub_ += "   |   last " + std::to_string(int(span)) + "s";
}

void App::build_tiles(double now) {
    auto latest = history_.latest();
    for (int i = 0; i < kGroupCount; ++i) {
        const Group& g = kGroups[i];
        Tile& t = tiles_[i];
        Metric m0 = g.metrics[0];
        Kind kind = kMetrics[m0].kind;
        t = {};
        if (!latest) {
            t.value = "--";
            continue;
        }
        if (std::string(g.id) == "gpu" && !gpu_.available()) {
            t.value = "n/a";
            t.peak = gpu_.reason();
            continue;
        }
        const SysValues& v = latest->sys;
        if (g.count == 2 && kMetrics[g.metrics[1]].kind == kind)          // disk, network
            t.value = std::string(theme::arrow_down()) + " " + format_value(kind, v[g.metrics[0]]) + "   " +
                      theme::arrow_up() + " " + format_value(kind, v[g.metrics[1]]);
        else if (m0 == MEM) {
            char pct[16];
            std::snprintf(pct, sizeof pct, "   %.0f%%", v[MEM] / std::max(1.0, double(sampler_.mem_total.load())) * 100);
            t.value = format_value(kind, v[MEM]) + pct;
        } else if (m0 == GPU) {
            char pct[16];
            std::snprintf(pct, sizeof pct, "%.0f%%   ", v[GPU]);
            t.value = pct + format_value(Kind::Bytes, v[VRAM]);
        } else {
            t.value = format_value(kind, v[m0]);
        }
        Peak p = history_.system_peak(m0);
        if (p.ts) t.peak = "peak " + format_value(kind, p.v) + " " + format_clock(p.ts);

        Metric same[2];
        for (int j = 0; j < g.count; ++j)
            if (kMetrics[g.metrics[j]].kind == kind) same[t.lines++] = g.metrics[j];
        t.ts = history_.system_series(same, t.lines, 60, now, t.y);
        for (int j = 0; j < t.lines; ++j)
            for (double y : t.y[j]) t.ymax = std::max(t.ymax, y * 1.1);
        if (kind == Kind::Percent && m0 != GPU) t.ymax = 100;
    }
}

// ================================================================== drawing

void App::frame() {
    double now = wall_now();
    if (ticked_.exchange(false) || dirty_ || (status_until_ && now >= status_until_)) {
        if (now >= status_until_) status_until_ = 0;
        dirty_ = false;
        rebuild(now);
    }
    const ImGuiViewport* vp = ImGui::GetMainViewport();
    ImGui::SetNextWindowPos(vp->Pos);
    ImGui::SetNextWindowSize(vp->Size);
    ImGui::Begin("Mimir", nullptr, ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_NoMove |
                                       ImGuiWindowFlags_NoBringToFrontOnFocus | ImGuiWindowFlags_NoSavedSettings);
    draw_header();
    draw_tiles();
    float footer = ImGui::GetTextLineHeightWithSpacing() + 4;
    float h = ImGui::GetContentRegionAvail().y - footer;
    ImGui::PushStyleColor(ImGuiCol_ChildBg, theme::BASE);
    ImGui::BeginChild("left", {left_width_, h}, ImGuiChildFlags_Borders);
    draw_table();
    ImGui::EndChild();
    ImGui::SameLine(0, 0);
    ImGui::InvisibleButton("split", {8, h});                // drag to resize the two panels
    if (ImGui::IsItemHovered() || ImGui::IsItemActive()) ImGui::SetMouseCursor(ImGuiMouseCursor_ResizeEW);
    if (ImGui::IsItemActive())
        left_width_ = std::clamp(left_width_ + ImGui::GetIO().MouseDelta.x, 420.0f, ImGui::GetWindowWidth() - 420);
    ImGui::SameLine(0, 0);
    ImGui::BeginChild("right", {0, h}, ImGuiChildFlags_Borders);
    draw_chart();
    ImGui::EndChild();
    ImGui::PopStyleColor();
    {
        Font f(theme::fonts.regular, theme::SMALL);
        text(theme::OVERLAY0, footer_);
    }
    draw_settings();
    draw_kill();
    ImGui::End();
}

void App::draw_header() {
    if (logo_) {
        ImGui::Image(ImTextureRef(ImTextureID(logo_)), {30, 30});
        ImGui::SameLine();
    }
    {
        Font f(theme::fonts.bold, theme::TITLE);
        ImGui::AlignTextToFramePadding();
        ImGui::TextUnformatted("Mimir");
    }
    ImGui::SameLine();
    ImGui::AlignTextToFramePadding();
    text(theme::SUBTEXT, "resource monitor");
    ImGui::SameLine(0, 24);

    std::string dot = theme::dot();
    if (!sampler_.last_error().empty())
        text(theme::RED, dot + " Sampling error");
    else if (sampler_.paused)
        text(theme::YELLOW, dot + " Paused");
    else {
        char buf[64];
        std::snprintf(buf, sizeof buf, " Sampling every %.1fs", settings_.sample_interval);
        text(theme::GREEN, dot + buf);
    }
    ImGui::SameLine(0, 12);
    if (net_.active())
        text(theme::TEAL, dot + " Network capture on (passive)");
    else if (!net_.error().empty())
        text(theme::RED, dot + " Network capture failed");
    else if (!net_.available())
        text(theme::YELLOW, dot + " Per-process network: " + net_.reason());
    else if (!settings_.network_capture)
        text(theme::SUBTEXT, dot + " Network capture off");
    else
        text(theme::SUBTEXT, dot + " Network capture starting");
    if (!net_.error().empty()) ImGui::SetItemTooltip("%s", net_.error().c_str());

    ImGui::SameLine(0, 24);
    if (button(sampler_.paused ? "Resume" : "Pause", theme::BLUE, theme::CRUST, 90)) {
        sampler_.paused = !sampler_.paused;
        dirty_ = true;
    }
    ImGui::SameLine();
    if (ImGui::Button("Top processes")) set_scope(Scope::Top);
    ImGui::SameLine();
    if (ImGui::Button("Export CSV")) export_csv();
    ImGui::SameLine();
    if (ImGui::Button("Clear history")) {
        history_.clear();
        selection_.reset();
        if (scope_ == Scope::Selected) scope_ = Scope::Top;
        flash("History and peaks cleared");
    }
    ImGui::SameLine();
    if (ImGui::Button("Settings")) open_settings_ = true;
    if (can_elevate() && !net_.available()) {
        ImGui::SameLine();
        if (button("Restart as admin for network", theme::alpha(theme::YELLOW, 40), theme::YELLOW)) {
            relaunch_ = true;
            glfwSetWindowShouldClose(window_, 1);
        }
        ImGui::SetItemTooltip("%s\nCapture is passive and never touches live traffic.", capture_hint().c_str());
    }
}

void App::draw_tiles() {
    const ImGuiStyle& st = ImGui::GetStyle();
    float w = (ImGui::GetContentRegionAvail().x - st.ItemSpacing.x * (kGroupCount - 1)) / kGroupCount;
    for (int i = 0; i < kGroupCount; ++i) {
        const Group& g = kGroups[i];
        const Tile& t = tiles_[i];
        bool active = i == group_;
        if (i) ImGui::SameLine();
        ImGui::PushStyleColor(ImGuiCol_ChildBg, active ? rgb(36, 37, 56) : theme::BASE);
        ImGui::PushStyleColor(ImGuiCol_Border, active ? theme::BLUE : theme::SURFACE0);
        ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, {10, 6});
        ImGui::PushStyleVar(ImGuiStyleVar_ItemSpacing, {6, 2});
        ImGui::BeginChild(g.id, {w, 92}, ImGuiChildFlags_Borders, ImGuiWindowFlags_NoScrollbar);
        bool clicked = ImGui::IsWindowHovered(ImGuiHoveredFlags_ChildWindows) && ImGui::IsMouseClicked(ImGuiMouseButton_Left);
        {
            Font f(theme::fonts.regular, theme::SMALL);
            text(theme::SUBTEXT, g.label);
            ImGui::SameLine(0, 10);
            text(theme::OVERLAY1, t.peak);
        }
        {
            Font f(theme::fonts.regular, theme::BIG);
            ImGui::TextUnformatted(t.value.c_str());
        }
        ImPlot::PushStyleVar(ImPlotStyleVar_PlotPadding, ImVec2(0, 0));
        ImPlot::PushStyleColor(ImPlotCol_PlotBg, 0u);
        ImPlot::PushStyleColor(ImPlotCol_PlotBorder, 0u);
        if (ImPlot::BeginPlot("##spark", {-1, -1}, ImPlotFlags_CanvasOnly | ImPlotFlags_NoInputs | ImPlotFlags_NoFrame)) {
            ImPlot::SetupAxes(nullptr, nullptr, ImPlotAxisFlags_NoDecorations, ImPlotAxisFlags_NoDecorations);
            ImPlot::SetupAxisLimits(ImAxis_X1, now_ - 60, now_, ImPlotCond_Always);
            ImPlot::SetupAxisLimits(ImAxis_Y1, 0, t.ymax, ImPlotCond_Always);
            for (int j = 0; j < t.lines; ++j) {
                uint32_t c = kMetrics[g.metrics[j]].color;
                ImPlot::PlotLine(j ? "##b" : "##a", t.ts.data(), t.y[j].data(), int(t.ts.size()),
                                 ImPlotSpec(ImPlotProp_LineColor, c, ImPlotProp_LineWeight, 1.5f, ImPlotProp_FillColor, c,
                                            ImPlotProp_FillAlpha, 0.18f, ImPlotProp_Flags, ImPlotLineFlags_Shaded));
            }
            ImPlot::EndPlot();
        }
        ImPlot::PopStyleColor(2);
        ImPlot::PopStyleVar();
        ImGui::EndChild();
        ImGui::PopStyleVar(2);
        ImGui::PopStyleColor(2);
        if (clicked) {
            group_ = i;                                  // a tile picks the resource; the scope stays
            settings_.chart_group = g.id;
            dirty_ = true;
        }
    }
}

void App::draw_table() {
    Settings& s = settings_;
    {
        Font f(theme::fonts.bold, theme::HEADING);
        ImGui::AlignTextToFramePadding();
        ImGui::TextUnformatted("Processes");
    }
    ImGui::SetItemTooltip("Click a row to chart it. Right-click a row or a legend entry to end that process.\n"
                          "Click a column header to sort.");
    ImGui::SameLine(0, 12);
    ImGui::SetNextItemWidth(220);
    if (ImGui::InputTextWithHint("##filter", "filter by name", filter_, sizeof filter_)) dirty_ = true;
    ImGui::SameLine();
    if (ImGui::Checkbox("Group by name", &s.group_by_name)) {
        selection_.reset();
        if (scope_ == Scope::Selected) scope_ = Scope::Top;
        dirty_ = true;
    }
    ImGui::SameLine();
    dirty_ |= ImGui::Checkbox("Hide idle", &s.hide_idle);

    Font f(theme::fonts.regular, theme::TABLE);
    ImGui::PushStyleVar(ImGuiStyleVar_ItemSpacing, {6, 0});
    int flags = ImGuiTableFlags_Sortable | ImGuiTableFlags_ScrollY | ImGuiTableFlags_ScrollX | ImGuiTableFlags_RowBg |
                ImGuiTableFlags_BordersInnerH | ImGuiTableFlags_SizingFixedFit;
    if (ImGui::BeginTable("procs", 3 + kColumnCount, flags)) {
        ImGui::TableSetupScrollFreeze(2, 1);
        ImGui::TableSetupColumn("#", ImGuiTableColumnFlags_NoSort);
        ImGui::TableSetupColumn("Process", ImGuiTableColumnFlags_NoSort);
        ImGui::TableSetupColumn("PID", ImGuiTableColumnFlags_NoSort);
        Metric sm = metric_by_id(s.sort_metric);
        Field sf = field_by_name(s.sort_field);
        for (int c = 0; c < kColumnCount; ++c) {
            int cf = s.sort_descending ? ImGuiTableColumnFlags_PreferSortDescending : ImGuiTableColumnFlags_PreferSortAscending;
            if (kColumns[c].metric == sm && kColumns[c].field == sf) cf |= ImGuiTableColumnFlags_DefaultSort;
            ImGui::TableSetupColumn(header_of(kColumns[c]).c_str(), cf, 0, ImGuiID(c));
        }
        ImGui::TableHeadersRow();
        if (ImGuiTableSortSpecs* specs = ImGui::TableGetSortSpecs(); specs && specs->SpecsDirty && specs->SpecsCount) {
            const Column& c = kColumns[specs->Specs[0].ColumnUserID];
            s.sort_metric = kMetrics[c.metric].id;
            s.sort_field = field_name(c.field);
            s.sort_descending = specs->Specs[0].SortDirection == ImGuiSortDirection_Descending;
            specs->SpecsDirty = false;
            dirty_ = true;
        }
        ImGuiListClipper clip;
        clip.Begin(int(table_.size()));
        while (clip.Step())
            for (int i = clip.DisplayStart; i < clip.DisplayEnd; ++i) {
                const TableLine& line = table_[i];
                ImGui::TableNextRow();
                ImGui::TableNextColumn();
                bool selected = scope_ == Scope::Selected && selection_ && selection_->key == line.target.key;
                ImGui::PushID(i);
                if (ImGui::Selectable(std::to_string(i + 1).c_str(), selected, ImGuiSelectableFlags_SpanAllColumns)) {
                    if (selected) {
                        set_scope(Scope::Top);
                    } else {
                        selection_ = line.target;
                        set_scope(Scope::Selected);
                    }
                }
                if (ImGui::IsItemClicked(ImGuiMouseButton_Right)) open_kill(line.target);
                ImGui::PopID();
                ImGui::TableNextColumn();
                text(line.color, line.shown);
                if (line.shown != line.target.label) ImGui::SetItemTooltip("%s", line.target.label.c_str());
                for (size_t c = 0; c < line.cells.size(); ++c) {
                    ImGui::TableNextColumn();
                    const Cell& cell = line.cells[c];
                    if (cell.fill >= 0) {
                        ImGui::ProgressBar(cell.fill, {bar_width_, 0}, cell.text.c_str());
                        continue;
                    }
                    text_right(cell.color, cell.text);
                    if (!cell.tip.empty()) ImGui::SetItemTooltip("%s", cell.tip.c_str());
                }
            }
        ImGui::EndTable();
    }
    ImGui::PopStyleVar();
}

void App::draw_chart() {
    {
        Font f(theme::fonts.bold, theme::HEADING);
        ImGui::TextUnformatted(chart_title_.c_str());
    }
    text(theme::SUBTEXT, chart_sub_);
    for (int i = 0; i < kGroupCount; ++i) {
        if (i) ImGui::SameLine();
        if (ImGui::RadioButton(kGroups[i].label, group_ == i)) {
            group_ = i;
            settings_.chart_group = kGroups[i].id;
            dirty_ = true;
        }
    }
    ImGui::SameLine(0, 20);
    const char* scopes[] = {"System", "Top processes", "Selected"};
    for (int i = 0; i < 3; ++i) {
        ImGui::SameLine();
        if (ImGui::RadioButton(scopes[i], int(scope_) == i)) {
            if (Scope(i) == Scope::Selected && !selection_)
                flash("Click a process row first");
            else
                set_scope(Scope(i));
        }
    }

    Font f(theme::fonts.regular, theme::SMALL);         // compact legend and axis text
    float h = ImGui::GetContentRegionAvail().y;
    float plot_h = panels_.size() > 1 ? (h - ImGui::GetStyle().ItemSpacing.y) / 2 : h;
    for (size_t pi = 0; pi < panels_.size(); ++pi) {
        const Panel& p = panels_[pi];
        const MetricInfo& m = kMetrics[p.primary];
        ImGui::PushID(int(pi));
        if (ImPlot::BeginPlot("##chart", {-1, plot_h}, ImPlotFlags_NoTitle | ImPlotFlags_NoMenus | ImPlotFlags_NoMouseText)) {
            std::string ylabel = std::string(m.label) + "  (" + axis_unit(m.kind) + ")";
            ImPlot::SetupAxes(nullptr, ylabel.c_str());
            ImPlot::SetupAxisLimits(ImAxis_X1, now_ - chart_span(), now_, ImPlotCond_Always);
            std::vector<const char*> labels;
            for (auto& l : tick_labels_) labels.push_back(l.c_str());
            if (!ticks_.empty()) ImPlot::SetupAxisTicks(ImAxis_X1, ticks_.data(), int(ticks_.size()), labels.data());
            ImPlot::SetupAxisLimits(ImAxis_Y1, 0, p.ymax, ImPlotCond_Always);
            ImPlot::SetupLegend(ImPlotLocation_NorthWest, ImPlotLegendFlags_NoMenus);
            for (const Line& l : p.lines) {
                ImPlot::PlotLine(l.id.c_str(), p.ts.data(), l.y.data(), int(p.ts.size()),
                                 ImPlotSpec(ImPlotProp_LineColor, l.color, ImPlotProp_LineWeight, 2.0f, ImPlotProp_FillColor,
                                            l.color, ImPlotProp_FillAlpha, 0.12f, ImPlotProp_Flags,
                                            l.shaded ? ImPlotLineFlags_Shaded : 0));
                if (l.target && ImPlot::BeginLegendPopup(l.id.c_str())) {    // right-click on a legend entry
                    if (ImGui::MenuItem(("End " + l.target->label + "...").c_str())) open_kill(*l.target);
                    ImPlot::EndLegendPopup();
                }
            }
            if (p.peak_line >= 0) {
                const std::vector<double>& y = p.lines[p.peak_line].y;
                size_t i = size_t(std::max_element(y.begin(), y.end()) - y.begin());
                std::string label = "peak " + format_value(m.kind, y[i] / axis_scale(m.kind)) + " at " + format_clock(p.ts[i]);
                ImPlot::Annotation(p.ts[i], y[i], theme::vec(theme::alpha(m.color, 200)), {12, -18}, true, "%s", label.c_str());
            }
            ImPlot::EndPlot();
        }
        ImGui::PopID();
    }
}

void App::draw_settings() {
    if (open_settings_) {
        ImGui::OpenPopup("Settings");
        open_settings_ = false;
    }
    if (!ImGui::BeginPopupModal("Settings", nullptr, ImGuiWindowFlags_AlwaysAutoResize)) return;
    Settings& s = settings_;
    bool changed = false;
    auto slider_int = [&](const char* label, int& v, int lo, int hi) {
        ImGui::SetNextItemWidth(280);
        changed |= ImGui::SliderInt(label, &v, lo, hi);
    };
    text(theme::BLUE, "Sampling");
    ImGui::SetNextItemWidth(280);
    float interval = float(s.sample_interval);
    if (ImGui::SliderFloat("interval (s)", &interval, 0.2f, 5.0f, "%.1f")) {
        s.sample_interval = interval;
        changed = true;
    }
    int minutes = s.history_seconds / 60;
    slider_int("history kept (min)", minutes, 1, 720);
    s.history_seconds = minutes * 60;
    ImGui::SetNextItemWidth(280);
    float threshold = float(s.cpu_threshold);
    if (ImGui::SliderFloat("ignore processes under (% of a core)", &threshold, 0, 10, "%.1f")) {
        s.cpu_threshold = threshold;
        changed = true;
    }
    ImGui::BeginDisabled(!net_.available());
    if (ImGui::Checkbox("passive network capture (admin only)", &s.network_capture)) {
        if (s.network_capture)
            net_.start();
        else
            net_.stop();
    }
    ImGui::EndDisabled();
    ImGui::Spacing();
    text(theme::BLUE, "Table");
    slider_int("ranking window (s)", s.ranking_window, 2, 600);
    slider_int("CPU column average (s)", s.cpu_smoothing, 1, 60);
    slider_int("rows", s.table_rows, 5, 200);
    ImGui::Spacing();
    text(theme::BLUE, "Chart");
    slider_int("chart span (s)", s.chart_seconds, 10, 3600);
    slider_int("chart span, one process (s)", s.selected_chart_seconds, 10, 3600);
    slider_int("top processes charted", s.chart_lines, 1, 10);
    ImGui::Spacing();
    text(theme::SUBTEXT, "Settings are saved to settings.json on exit.");
    if (ImGui::Button("Close", {100, 0})) ImGui::CloseCurrentPopup();
    ImGui::EndPopup();
    if (changed) {
        s.validate();
        history_.set_maxlen(s.max_ticks());
        sampler_.interval = s.sample_interval;
        sampler_.cpu_threshold = s.cpu_threshold;
        dirty_ = true;
    }
}

void App::open_kill(const Target& t) {
    kill_ = t;
    kill_children_ = true;
    open_kill_ = true;
}

void App::draw_kill() {
    if (open_kill_) {
        ImGui::OpenPopup("End process");
        ImVec2 mouse = ImGui::GetMousePos();
        ImGui::SetNextWindowPos({mouse.x - 20, mouse.y - 20});
        open_kill_ = false;
    }
    if (!kill_ || !ImGui::BeginPopupModal("End process", nullptr,
                                          ImGuiWindowFlags_NoTitleBar | ImGuiWindowFlags_AlwaysAutoResize))
        return;
    size_t n = kill_->pids.size();
    {
        Font f(theme::fonts.bold, theme::HEADING);
        std::string title = n == 1 ? "End " + kill_->name + "?" : "End all " + std::to_string(n) + " " + kill_->name + " processes?";
        ImGui::TextUnformatted(title.c_str());
    }
    std::string pids;
    for (size_t i = 0; i < n && i < 8; ++i) pids += (i ? ", " : "") + std::to_string(kill_->pids[i]);
    text(theme::SUBTEXT, "PID " + pids + (n > 8 ? " ..." : "") + "\nTerminated immediately, unsaved work in it is lost.");
    ImGui::Checkbox("also end child processes", &kill_children_);
    ImGui::Spacing();
    if (button("End process", theme::RED, theme::CRUST, 140)) {
        KillReport r = kill_processes(kill_->pids, kill_children_);
        log_info("kill %s -> %s", kill_->label.c_str(), r.summary().c_str());
        flash(kill_->name + ": " + r.summary(), 8);
        if (selection_ && selection_->key == kill_->key) set_scope(Scope::Top);
        kill_.reset();
        ImGui::CloseCurrentPopup();
    }
    ImGui::SameLine();
    if (ImGui::Button("Cancel", {100, 0})) {
        kill_.reset();
        ImGui::CloseCurrentPopup();
    }
    ImGui::EndPopup();
}

void App::set_scope(Scope s) {
    scope_ = s == Scope::Selected && !selection_ ? Scope::Top : s;
    dirty_ = true;
}

void App::flash(const std::string& msg, double seconds) {
    status_ = msg;
    status_until_ = wall_now() + seconds;
    dirty_ = true;
}

void App::export_csv() {
    namespace fs = std::filesystem;
    std::error_code ec;
    fs::path dir = fs::path(app_dir()) / "exports";
    fs::create_directories(dir, ec);
    double span = settings_.history_seconds, now = wall_now();
    std::string name = "system";
    std::vector<Metric> metrics;
    std::vector<double> ts, cols[kProcMetrics];
    if (scope_ == Scope::Selected && selection_) {
        name.clear();
        for (char c : selection_->label) name += std::isalnum(static_cast<unsigned char>(c)) ? c : '_';
        name = name.substr(0, 40);
        for (int i = 0; i < kProcMetrics; ++i) metrics.push_back(Metric(i));
        ts = history_.entity_series(selection_->key, selection_->pids, metrics.data(), kProcMetrics, span, now, cols);
    } else {
        for (int i = 0; i < kSysMetrics; ++i) metrics.push_back(Metric(i));
        ts = history_.system_series(metrics.data(), kSysMetrics, span, now, cols);
    }
    fs::path path = dir / ("mimir_" + name + "_" + format_clock(now, "%Y%m%d_%H%M%S") + ".csv");
    std::ofstream f(path);
    f << "epoch,time";
    for (Metric m : metrics) f << ',' << kMetrics[m].label;
    f << '\n';
    char buf[64];
    for (size_t i = 0; i < ts.size(); ++i) {
        std::snprintf(buf, sizeof buf, "%.3f,", ts[i]);
        f << buf << format_clock(ts[i], "%Y-%m-%dT%H:%M:%S");
        for (size_t j = 0; j < metrics.size(); ++j) {
            std::snprintf(buf, sizeof buf, ",%.3f", cols[j][i]);
            f << buf;
        }
        f << '\n';
    }
    flash(f ? "Exported " + path.filename().string() : "Export failed: " + path.string());
}

void App::save_shot(const std::string& file) {
    int w, h;
    glfwGetFramebufferSize(window_, &w, &h);
    std::vector<unsigned char> px(size_t(w) * h * 4);
    glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE, px.data());
    stbi_flip_vertically_on_write(1);
    stbi_write_png(file.c_str(), w, h, 4, px.data(), w * 4);
    log_info("saved %s", file.c_str());
}

// ================================================================== run

int App::run(double shot_after, const std::string& shot_file) {
    if (!glfwInit()) {
        log_error("could not initialise GLFW (no display?)");
        return 1;
    }
    window_ = glfwCreateWindow(settings_.window_width, settings_.window_height, "Mimir", nullptr, nullptr);
    if (!window_) {
        log_error("could not create the window");
        glfwTerminate();
        return 1;
    }
    glfwSetWindowSizeLimits(window_, 1000, 650, GLFW_DONT_CARE, GLFW_DONT_CARE);
    glfwMakeContextCurrent(window_);
    glfwSwapInterval(1);
    // Input marks the UI "hot"; ImGui's backend chains to these callbacks.
    glfwSetCursorPosCallback(window_, [](GLFWwindow*, double, double) { mark_input(); });
    glfwSetMouseButtonCallback(window_, [](GLFWwindow*, int, int, int) { mark_input(); });
    glfwSetScrollCallback(window_, [](GLFWwindow*, double, double) { mark_input(); });
    glfwSetKeyCallback(window_, [](GLFWwindow*, int, int, int, int) { mark_input(); });
    glfwSetCharCallback(window_, [](GLFWwindow*, unsigned) { mark_input(); });
    glfwSetWindowSizeCallback(window_, [](GLFWwindow*, int, int) { mark_input(); });
    glfwSetWindowFocusCallback(window_, [](GLFWwindow*, int) { mark_input(); });

    std::string asset = app_dir() + "/assets/mimir.png";
    int iw, ih, ic;
    if (unsigned char* px = stbi_load(asset.c_str(), &iw, &ih, &ic, 4)) {
        GLFWimage icon{iw, ih, px};
        glfwSetWindowIcon(window_, 1, &icon);
        glGenTextures(1, &logo_);
        glBindTexture(GL_TEXTURE_2D, logo_);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, iw, ih, 0, GL_RGBA, GL_UNSIGNED_BYTE, px);
        stbi_image_free(px);
    }

    IMGUI_CHECKVERSION();
    ImGui::CreateContext();
    ImPlot::CreateContext();
    theme::apply();
    ImGui_ImplGlfw_InitForOpenGL(window_, true);
    ImGui_ImplOpenGL3_Init();
    log_info("OpenGL: %s", reinterpret_cast<const char*>(glGetString(GL_RENDERER)));
    void* native = nullptr;
#ifdef _WIN32
    native = glfwGetWin32Window(window_);
#endif
    style_window(native);

    sampler_.interval = settings_.sample_interval;
    sampler_.cpu_threshold = settings_.cpu_threshold;
    sampler_.start();
    if (settings_.network_capture) net_.start();
    double shot_at = shot_after > 0 ? wall_now() + shot_after : 0;
    mark_input();

    while (!glfwWindowShouldClose(window_)) {
        double now = wall_now();
        // Sleep until input or a new sample (the sampler posts an empty event). While hot,
        // wake every 0.1 s so hover states, tooltips and the caret keep up.
        double wait = now < g_hot_until ? 0.1 : 1e9;
        if (shot_at) wait = std::min(wait, std::max(0.0, shot_at - now));
        if (wait < 1e9)
            glfwWaitEventsTimeout(wait);
        else
            glfwWaitEvents();
        if (!window_alive(native)) {                        // a host (Dashpan) took our window down with itself
            log_warn("window vanished; exiting");
            break;
        }
        if (glfwGetWindowAttrib(window_, GLFW_ICONIFIED)) continue;
        ImGui_ImplOpenGL3_NewFrame();
        ImGui_ImplGlfw_NewFrame();
        ImGui::NewFrame();
        frame();
        ImGui::Render();
        int w, h;
        glfwGetFramebufferSize(window_, &w, &h);
        glViewport(0, 0, w, h);
        ImVec4 bg = theme::vec(theme::MANTLE);
        glClearColor(bg.x, bg.y, bg.z, 1);
        glClear(GL_COLOR_BUFFER_BIT);
        ImGui_ImplOpenGL3_RenderDrawData(ImGui::GetDrawData());
        if (shot_at && wall_now() >= shot_at) {
            save_shot(shot_file);
            glfwSetWindowShouldClose(window_, 1);
        }
        glfwSwapBuffers(window_);
    }

    glfwGetWindowSize(window_, &settings_.window_width, &settings_.window_height);
    settings_.left_panel_width = int(left_width_);
    settings_.validate();
    net_.stop();
    sampler_.stop();
    ImGui_ImplOpenGL3_Shutdown();
    ImGui_ImplGlfw_Shutdown();
    ImPlot::DestroyContext();
    ImGui::DestroyContext();
    glfwDestroyWindow(window_);
    glfwTerminate();
    if (relaunch_) relaunch_elevated(argc_, argv_);
    return 0;
}
