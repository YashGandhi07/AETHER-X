// Portable fallback for platforms without a dedicated implementation yet
// (Linux: /proc/cpuinfo + sysconf; Windows: GetLogicalProcessorInformationEx +
// GlobalMemoryStatusEx). Reports only what standard C++ can determine.

#include <thread>

#include "platform.hpp"

namespace aether::platform {

void fill_system_info(SystemInfo& info) {
#if defined(__linux__)
    info.os_family = "Linux";
#elif defined(_WIN32)
    info.os_family = "Windows";
#else
    info.os_family = "unknown";
#endif
    // hardware_concurrency() returns 0 when unknown, which must not be reported as 0 CPUs.
    if (const unsigned logical = std::thread::hardware_concurrency(); logical > 0) {
        info.logical_cpus = logical;
    }
}

}  // namespace aether::platform
