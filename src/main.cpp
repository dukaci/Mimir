// Mimir: per-process resource monitor with history and peaks.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

#include "gpu.h"
#include "history.h"
#include "log.h"
#include "net.h"
#include "sampler.h"
#include "settings.h"
#include "sys.h"
#include "ui.h"

static const char* kUsage =
    "usage: mimir [--interval SECONDS] [--no-network] [--log-level debug|info|warn|error]\n"
    "             [--shot SECONDS [--shot-file FILE]] [--version]\n"
    "  --interval    sampling interval (overrides settings.json)\n"
    "  --no-network  never start per-process network capture\n"
    "  --shot        debug: save a screenshot after SECONDS and exit\n";

int main(int argc, char** argv) {
    double interval = 0, shot = 0;
    bool no_network = false;
    std::string shot_file = "mimir_shot.png";
    LogLevel level = LogLevel::Info;
    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        const char* value = i + 1 < argc ? argv[i + 1] : "";
        if (a == "--interval") interval = std::atof(argv[++i < argc ? i : 0]);
        else if (a == "--shot") shot = std::atof(argv[++i < argc ? i : 0]);
        else if (a == "--shot-file") shot_file = argv[++i < argc ? i : 0];
        else if (a == "--no-network") no_network = true;
        else if (a == "--log-level") {
            std::string l = value;
            ++i;
            level = l == "debug" ? LogLevel::Debug : l == "warn" ? LogLevel::Warn : l == "error" ? LogLevel::Error : LogLevel::Info;
        } else if (a == "--version") {
            std::printf("mimir %s\n", MIMIR_VERSION);
            return 0;
        } else {
            std::fputs(kUsage, stderr);
            return a == "--help" || a == "-h" ? 0 : 2;
        }
    }

    std::string dir = app_dir();
    log_open((dir + "/logs/mimir.log").c_str(), level);
    std::string settings_file = dir + "/settings.json";
    Settings settings = Settings::load(settings_file);
    if (interval > 0) {
        settings.sample_interval = interval;
        settings.validate();
    }
    if (no_network) settings.network_capture = false;

    History history(settings.max_ticks());
    auto net = Capture::create();
    Gpu gpu;
    App* app = nullptr;
    Sampler sampler(history, *net, gpu, [&] {
        if (app) app->on_tick();
    });
    App ui(settings, history, sampler, *net, gpu, argc, argv);
    app = &ui;
    log_info("mimir %s starting (network=%s, gpu=%s)", MIMIR_VERSION, net->available() ? "available" : net->reason().c_str(),
             gpu.available() ? gpu.name().c_str() : gpu.reason().c_str());
    int rc = ui.run(shot, shot_file);
    settings.save(settings_file);
    return rc;
}
