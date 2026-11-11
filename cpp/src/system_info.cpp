#include "aether/system_info.hpp"

#include <format>

#include "platform/platform.hpp"

// Injected by CMake for aether_core; fall back so the file still compiles
// outside the project build (e.g. a quick manual compile).
#ifndef AETHER_BUILD_TYPE
#define AETHER_BUILD_TYPE "unknown"
#endif

namespace aether {
namespace {

// The architecture this binary was compiled for. Under Rosetta it differs
// from the hardware, which process_translated reports separately.
std::string compile_time_architecture() {
#if defined(__aarch64__) || defined(_M_ARM64)
    return "arm64";
#elif defined(__x86_64__) || defined(_M_X64)
    return "x86_64";
#elif defined(__i386__) || defined(_M_IX86)
    return "x86";
#else
    return "unknown";
#endif
}

std::string cxx_standard_name() {
    // MSVC reports 199711L in __cplusplus unless /Zc:__cplusplus is given.
#if defined(_MSVC_LANG)
    constexpr long standard = _MSVC_LANG;
#else
    constexpr long standard = __cplusplus;
#endif
    if constexpr (standard > 202302L) {
        return std::format("C++26 (draft, {})", standard);
    } else if constexpr (standard == 202302L) {
        return "C++23";
    } else if constexpr (standard > 202002L) {
        return std::format("C++23 (draft, {})", standard);
    } else if constexpr (standard == 202002L) {
        return "C++20";
    } else {
        return std::format("pre-C++20 ({})", standard);
    }
}

}  // namespace

SystemInfo detect_system_info() {
    SystemInfo info;
    info.architecture = compile_time_architecture();
    platform::fill_system_info(info);
    return info;
}

BuildInfo build_info() {
    BuildInfo build;
#if defined(__clang__) && defined(__apple_build_version__)
    build.compiler_id = "AppleClang";
    build.compiler_version =
        std::format("{}.{}.{}", __clang_major__, __clang_minor__, __clang_patchlevel__);
#elif defined(__clang__)
    build.compiler_id = "Clang";
    build.compiler_version =
        std::format("{}.{}.{}", __clang_major__, __clang_minor__, __clang_patchlevel__);
#elif defined(__GNUC__)
    build.compiler_id = "GCC";
    build.compiler_version = std::format("{}.{}.{}", __GNUC__, __GNUC_MINOR__, __GNUC_PATCHLEVEL__);
#elif defined(_MSC_VER)
    build.compiler_id = "MSVC";
    build.compiler_version = std::format("{}", _MSC_FULL_VER);
#else
    build.compiler_id = "unknown";
    build.compiler_version = "unknown";
#endif
    build.cxx_standard = cxx_standard_name();
    build.build_type = AETHER_BUILD_TYPE;
#ifdef AETHER_CXX_FLAGS
    build.cmake_cxx_flags = AETHER_CXX_FLAGS;
#endif
#ifdef NDEBUG
    build.assertions_enabled = false;
#else
    build.assertions_enabled = true;
#endif
    return build;
}

}  // namespace aether
