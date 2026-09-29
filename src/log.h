// printf-style log to stderr and logs/mimir.log.
#pragma once

enum class LogLevel { Debug, Info, Warn, Error };

void log_open(const char* path, LogLevel level);     // path may be null: stderr only
void log_write(LogLevel level, const char* fmt, ...)
#if defined(__GNUC__)
    __attribute__((format(printf, 2, 3)))
#endif
    ;

#define log_debug(...) log_write(LogLevel::Debug, __VA_ARGS__)
#define log_info(...) log_write(LogLevel::Info, __VA_ARGS__)
#define log_warn(...) log_write(LogLevel::Warn, __VA_ARGS__)
#define log_error(...) log_write(LogLevel::Error, __VA_ARGS__)
