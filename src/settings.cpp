#include "settings.h"

#include <algorithm>
#include <fstream>
#include <map>
#include <sstream>

#include "log.h"

namespace {

// {"key": value, ...} with numbers, true/false and plain strings: all settings.json needs.
std::map<std::string, std::string> parse_flat_json(const std::string& s) {
    std::map<std::string, std::string> out;
    size_t i = 0;
    while ((i = s.find('"', i)) != std::string::npos) {
        size_t k = s.find('"', i + 1);
        size_t colon = k == std::string::npos ? k : s.find(':', k);
        if (colon == std::string::npos) break;
        std::string key = s.substr(i + 1, k - i - 1);
        size_t v = s.find_first_not_of(" \t\r\n", colon + 1);
        if (v == std::string::npos) break;
        size_t end;
        if (s[v] == '"') {
            end = s.find('"', v + 1);
            if (end == std::string::npos) break;
            out[key] = s.substr(v + 1, end - v - 1);
            ++end;
        } else {
            end = s.find_first_of(",}\r\n", v);
            std::string raw = s.substr(v, end - v);
            raw.erase(raw.find_last_not_of(" \t") + 1);
            out[key] = raw;
        }
        i = end;
    }
    return out;
}

void assign(double& m, const std::string& v) { m = std::stod(v); }
void assign(int& m, const std::string& v) { m = int(std::stod(v)); }
void assign(bool& m, const std::string& v) { m = v == "true"; }
void assign(std::string& m, const std::string& v) { m = v; }

void write(std::ostream& o, double v) { o << v; }
void write(std::ostream& o, int v) { o << v; }
void write(std::ostream& o, bool v) { o << (v ? "true" : "false"); }
void write(std::ostream& o, const std::string& v) { o << '"' << v << '"'; }

template <class T> void clamp(T& v, T lo, T hi) { v = std::clamp(v, lo, hi); }

}  // namespace

void Settings::validate() {
    clamp(sample_interval, 0.2, 10.0);
    clamp(history_seconds, 60, 24 * 3600);
    clamp(cpu_threshold, 0.0, 100.0);
    clamp(ranking_window, 2, 3600);
    clamp(cpu_smoothing, 1, 60);
    clamp(table_rows, 5, 500);
    clamp(chart_seconds, 10, 24 * 3600);
    clamp(selected_chart_seconds, 10, 24 * 3600);
    clamp(chart_lines, 1, 10);
    clamp(window_width, 900, 8000);
    clamp(window_height, 600, 5000);
    clamp(left_panel_width, 420, 3000);
    if (sort_field != "current" && sort_field != "average" && sort_field != "maximum" && sort_field != "peak")
        sort_field = "current";
}

Settings Settings::load(const std::string& path) {
    Settings s;
    std::ifstream f(path);
    if (f) {
        std::stringstream buf;
        buf << f.rdbuf();
        auto values = parse_flat_json(buf.str());
        fields(s, [&](const char* key, auto& member) {
            auto it = values.find(key);
            if (it == values.end()) return;
            try {
                assign(member, it->second);
            } catch (const std::exception&) {
                log_warn("settings: bad value for %s", key);
            }
        });
    }
    s.validate();
    return s;
}

void Settings::save(const std::string& path) const {
    std::ostringstream o;
    o << "{\n";
    bool first = true;
    fields(*this, [&](const char* key, const auto& member) {
        o << (first ? "" : ",\n") << "  \"" << key << "\": ";
        write(o, member);
        first = false;
    });
    o << "\n}\n";
    std::ofstream f(path, std::ios::binary);
    if (!(f << o.str())) log_warn("could not save %s", path.c_str());
}
