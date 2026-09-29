// Dear ImGui front end. Frames are event-driven: the loop sleeps until input arrives
// or the sampler posts a new tick, and the view model is rebuilt only on a change.
#pragma once

#include <atomic>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

#include "gpu.h"
#include "history.h"
#include "net.h"
#include "sampler.h"
#include "settings.h"

struct GLFWwindow;

class App {
public:
    App(Settings& settings, History& history, Sampler& sampler, Capture& net, Gpu& gpu, int argc, char** argv);
    int run(double shot_after, const std::string& shot_file);
    void on_tick();                                  // any thread: a new sample is in History

private:
    enum class Scope { System, Top, Selected };
    struct Target {                                  // a process or group to chart or end
        uint64_t key;
        std::string name, label;
        std::vector<uint32_t> pids;
    };
    struct Cell {
        std::string text, tip;
        uint32_t color = 0;
        float fill = -1;                             // >= 0: drawn as a bar
    };
    struct TableLine {
        Target target;
        std::string shown;                           // label cut to the Process column width
        uint32_t color;
        std::vector<Cell> cells;
    };
    struct Line {
        std::string id;                              // ImPlot label: "text##n"
        std::optional<Target> target;                // what a legend right-click ends
        uint32_t color;
        bool shaded;
        std::vector<double> y;
    };
    struct Panel {
        Metric primary;
        std::vector<double> ts;
        std::vector<Line> lines;
        double ymax = 1;
        int peak_line = -1;                          // annotated line, -1 for none
    };
    struct Tile {
        std::string value, peak;
        std::vector<double> ts, y[2];
        int lines = 0;
        double ymax = 1;
    };

    // model
    void rebuild(double now);
    void build_table(const std::vector<EntityRow>& rows, const std::unordered_map<uint64_t, uint32_t>& charted);
    void build_chart(double now, const std::unordered_map<uint64_t, uint32_t>& colors);
    void build_tiles(double now);
    std::unordered_map<uint64_t, uint32_t> assign_colors(const std::vector<uint64_t>& keys, double now);
    Target target_of(const EntityRow& r) const;
    double chart_span() const;

    // drawing
    void frame();
    void draw_header();
    void draw_tiles();
    void draw_table();
    void draw_chart();
    void draw_settings();
    void draw_kill();
    void open_kill(const Target& t);
    void set_scope(Scope s);
    void flash(const std::string& msg, double seconds = 4);
    void export_csv();
    void save_shot(const std::string& file);

    Settings& settings_;
    History& history_;
    Sampler& sampler_;
    Capture& net_;
    Gpu& gpu_;
    int argc_;
    char** argv_;
    GLFWwindow* window_ = nullptr;
    unsigned logo_ = 0;

    // view state
    Scope scope_ = Scope::Top;
    int group_ = 0;
    std::optional<Target> selection_;
    char filter_[128] = "";
    std::string status_;
    double status_until_ = 0;
    std::optional<Target> kill_;
    bool kill_children_ = true, open_kill_ = false, open_settings_ = false, relaunch_ = false;
    float left_width_;
    std::unordered_map<uint64_t, std::pair<int, double>> color_slots_;   // key -> palette index, last charted

    // model, rebuilt by rebuild()
    std::atomic<bool> ticked_{true};
    bool dirty_ = true;
    std::vector<TableLine> table_;
    float bar_width_ = 60;
    std::vector<EntityRow> top_[2];
    std::vector<Panel> panels_;
    std::string chart_title_, chart_sub_;
    std::vector<double> ticks_;                      // chart x ticks (epoch seconds) and their clock labels
    std::vector<std::string> tick_labels_;
    Tile tiles_[kGroupCount];
    std::string footer_;
    double now_ = 0;
};
