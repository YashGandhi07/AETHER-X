// macOS hardware detection via sysctlbyname(3) and uname(3).

#include <sys/sysctl.h>
#include <sys/utsname.h>

#include <cstdint>
#include <cstring>
#include <limits>
#include <optional>
#include <string>

#include "platform.hpp"

namespace aether::platform {
namespace {

std::optional<std::string> sysctl_string(const char* name) {
    std::size_t size = 0;
    if (sysctlbyname(name, nullptr, &size, nullptr, 0) != 0 || size == 0) {
        return std::nullopt;
    }
    std::string value(size, '\0');
    if (sysctlbyname(name, value.data(), &size, nullptr, 0) != 0) {
        return std::nullopt;
    }
    value.resize(strnlen(value.data(), size));  // drop the trailing NUL
    if (value.empty()) {
        return std::nullopt;
    }
    return value;
}

// sysctl integers are 4 or 8 bytes depending on the key (hw.logicalcpu is
// 4, hw.memsize and hw.cachelinesize are 8), so dispatch on the returned size.
std::optional<std::uint64_t> sysctl_unsigned(const char* name) {
    unsigned char buffer[8] = {};
    std::size_t size = sizeof buffer;
    if (sysctlbyname(name, buffer, &size, nullptr, 0) != 0) {
        return std::nullopt;
    }
    if (size == sizeof(std::int32_t)) {
        std::int32_t value = 0;
        std::memcpy(&value, buffer, sizeof value);
        return value >= 0 ? std::optional<std::uint64_t>(static_cast<std::uint64_t>(value))
                          : std::nullopt;
    }
    if (size == sizeof(std::int64_t)) {
        std::int64_t value = 0;
        std::memcpy(&value, buffer, sizeof value);
        return value >= 0 ? std::optional<std::uint64_t>(static_cast<std::uint64_t>(value))
                          : std::nullopt;
    }
    return std::nullopt;
}

std::optional<std::uint32_t> sysctl_count(const char* name) {
    const auto value = sysctl_unsigned(name);
    if (!value || *value > std::numeric_limits<std::uint32_t>::max()) {
        return std::nullopt;
    }
    return static_cast<std::uint32_t>(*value);
}

}  // namespace

void fill_system_info(SystemInfo& info) {
    info.os_family = "macOS";
    info.os_version = sysctl_string("kern.osproductversion");
    info.os_build = sysctl_string("kern.osversion");

    if (utsname name{}; uname(&name) == 0 && name.release[0] != '\0') {
        info.kernel_release = name.release;
    }

    // The key only exists on Apple Silicon (and recent Intel macOS); absent
    // means the question does not apply, so leave the field empty.
    if (const auto translated = sysctl_unsigned("sysctl.proc_translated")) {
        info.process_translated = *translated == 1;
    }

    info.cpu_model = sysctl_string("machdep.cpu.brand_string");
    info.logical_cpus = sysctl_count("hw.logicalcpu");
    info.physical_cpus = sysctl_count("hw.physicalcpu");
    info.cache_line_bytes = sysctl_unsigned("hw.cachelinesize");
    info.memory_bytes = sysctl_unsigned("hw.memsize");

    // Heterogeneous cores (Apple Silicon): hw.perflevelN.* for N < hw.nperflevels.
    if (const auto levels = sysctl_count("hw.nperflevels")) {
        for (std::uint32_t level = 0; level < *levels; ++level) {
            const std::string prefix = "hw.perflevel" + std::to_string(level) + ".";
            const auto name = sysctl_string((prefix + "name").c_str());
            if (!name) {
                continue;
            }
            info.core_types.push_back(
                {.name = *name, .physical_cpus = sysctl_count((prefix + "physicalcpu").c_str())});
        }
    }
}

}  // namespace aether::platform
