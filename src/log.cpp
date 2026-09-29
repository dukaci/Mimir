#include "log.h"

#include <cstdarg>
#include <cstdio>
#include <filesystem>
#include <mutex>
#include <string>

#include "metrics.h"

namespace {

std::mutex mu;
std::FILE* file = nullptr;
LogLevel min_level = LogLevel::Info;
const char* names[] = {"DEBUG", "INFO", "WARN", "ERROR"};

}  // namespace

void log_open(const char* path, LogLevel level) {
    min_level = level;
    if (!path) return;
    std::error_code ec;
    std::filesystem::create_directories(std::filesystem::path(path).parent_path(), ec);
    if (std::filesystem::file_size(path, ec) > 1'000'000)     // keep one previous file, as a 1 MB rotation
        std::filesystem::rename(path, std::string(path) + ".1", ec);
    file = std::fopen(path, "a");
}

void log_write(LogLevel level, const char* fmt, ...) {
    if (level < min_level) return;
    char msg[1024];
    va_list args;
    va_start(args, fmt);
    std::vsnprintf(msg, sizeof msg, fmt, args);
    va_end(args);
    std::string line = format_clock(double(std::time(nullptr)), "%Y-%m-%d %H:%M:%S ") + names[int(level)] + " " +
                       msg + "\n";
    std::lock_guard lock(mu);
    std::fputs(line.c_str(), stderr);
    if (file) {
        std::fputs(line.c_str(), file);
        std::fflush(file);
    }
}
