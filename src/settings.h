// User settings, kept as flat JSON in settings.json.
#pragma once

#include <cstddef>
#include <string>

struct Settings {
    // sampling
    double sample_interval = 1.0;       // seconds between samples
    int history_seconds = 1800;         // history kept
    double cpu_threshold = 0.0;         // % of one core below which a quiet process is not stored
    bool network_capture = true;        // passive per-process network (admin / capabilities)

    // table
    int ranking_window = 30;            // seconds aggregated for the table
    int cpu_smoothing = 5;              // seconds averaged for the CPU column
    bool group_by_name = true;
    bool hide_idle = true;
    int table_rows = 40;
    std::string sort_metric = "cpu";
    std::string sort_field = "current"; // current | average | maximum | peak
    bool sort_descending = true;

    // chart
    int chart_seconds = 120;
    int selected_chart_seconds = 300;   // span when one process is selected
    int chart_lines = 8;                // top N processes charted
    std::string chart_group = "cpu";

    // window
    int window_width = 1600;
    int window_height = 960;
    int left_panel_width = 760;

    void validate();
    size_t max_ticks() const { return size_t(history_seconds / sample_interval) + 2; }

    static Settings load(const std::string& path);
    void save(const std::string& path) const;

    // Calls f(key, member) for every field: load and save share this one list.
    template <class S, class F> static void fields(S& s, F&& f) {
        f("sample_interval", s.sample_interval);
        f("history_seconds", s.history_seconds);
        f("cpu_threshold", s.cpu_threshold);
        f("network_capture", s.network_capture);
        f("ranking_window", s.ranking_window);
        f("cpu_smoothing", s.cpu_smoothing);
        f("group_by_name", s.group_by_name);
        f("hide_idle", s.hide_idle);
        f("table_rows", s.table_rows);
        f("sort_metric", s.sort_metric);
        f("sort_field", s.sort_field);
        f("sort_descending", s.sort_descending);
        f("chart_seconds", s.chart_seconds);
        f("selected_chart_seconds", s.selected_chart_seconds);
        f("chart_lines", s.chart_lines);
        f("chart_group", s.chart_group);
        f("window_width", s.window_width);
        f("window_height", s.window_height);
        f("left_panel_width", s.left_panel_width);
    }
};
