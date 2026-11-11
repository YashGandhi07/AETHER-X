#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace aether {

// One class of CPU core as named by the OS, e.g. Apple's "Super" or
// "Efficiency" performance levels. Empty on platforms that do not report it.
struct CpuCoreType {
    std::string name;
    std::optional<std::uint32_t> physical_cpus;
};

// Hardware and OS facts for the machine a benchmark ran on. Every field that
// cannot be detected stays empty and is exported as JSON null; nothing is
// guessed. The hostname and user name are deliberately not collected.
struct SystemInfo {
    std::string os_family;                    // "macOS", "Linux", "Windows" or "unknown"
    std::optional<std::string> os_version;    // e.g. "26.6.2"
    std::optional<std::string> os_build;      // e.g. "25G83"
    std::optional<std::string> kernel_release;  // e.g. "25.6.0"
    std::string architecture;                 // target of this binary: "arm64", "x86_64", ...
    // True when an x86_64 binary is running under Rosetta 2 on Apple Silicon,
    // which changes performance completely.
    std::optional<bool> process_translated;
    std::optional<std::string> cpu_model;
    std::optional<std::uint32_t> logical_cpus;
    std::optional<std::uint32_t> physical_cpus;
    std::vector<CpuCoreType> core_types;
    std::optional<std::uint64_t> cache_line_bytes;
    std::optional<std::uint64_t> memory_bytes;
};

// How this code was compiled. Values come from the compiler's predefined
// macros and from CMake at configure time, so they describe the actual binary.
struct BuildInfo {
    std::string compiler_id;       // "AppleClang", "Clang", "GCC", "MSVC" or "unknown"
    std::string compiler_version;  // e.g. "21.0.0"
    std::string cxx_standard;      // e.g. "C++20"
    std::string build_type;        // CMake configuration, e.g. "Release"
    // CMAKE_CXX_FLAGS plus the configuration-specific flags (e.g. "-O3 -DNDEBUG").
    // Empty for multi-config generators, where it is not known at configure time.
    std::optional<std::string> cmake_cxx_flags;
    bool assertions_enabled = false;  // whether NDEBUG was undefined
};

// Detects the running system. Never throws for missing information: unknown
// fields are left empty. Platform-specific queries live in src/platform/.
SystemInfo detect_system_info();

BuildInfo build_info();

}  // namespace aether
