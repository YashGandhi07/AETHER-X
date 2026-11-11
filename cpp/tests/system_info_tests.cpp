// Tests for hardware detection, build information and experiment ids.
//
// Detection tests check consistency against independent sources (standard
// C++, compile-time macros) rather than hard-coding this machine's values.

#include <cstddef>
#include <set>
#include <string>
#include <thread>

#include "aether/experiment_id.hpp"
#include "aether/system_info.hpp"
#include "test_harness.hpp"

namespace {

using aether::test::check;

void test_detect_common_fields() {
    const aether::SystemInfo info = aether::detect_system_info();
    check(!info.os_family.empty(), "os_family always set");
    check(!info.architecture.empty(), "architecture always set");
#if defined(__aarch64__) || defined(_M_ARM64)
    check(info.architecture == "arm64", "architecture matches the compiled target");
#elif defined(__x86_64__) || defined(_M_X64)
    check(info.architecture == "x86_64", "architecture matches the compiled target");
#endif

    // Cross-check with the standard library's independent query.
    const unsigned standard = std::thread::hardware_concurrency();
    if (standard > 0) {
        check(info.logical_cpus == standard, "logical CPUs agree with hardware_concurrency()");
    }
    if (info.logical_cpus && info.physical_cpus) {
        check(*info.physical_cpus >= 1 && *info.physical_cpus <= *info.logical_cpus,
              "1 <= physical CPUs <= logical CPUs");
    }
    // Optional fields that are present must be meaningful, never placeholders.
    check(!info.cpu_model || !info.cpu_model->empty(), "cpu_model is absent or non-empty");
    check(!info.os_version || !info.os_version->empty(), "os_version is absent or non-empty");
    check(!info.memory_bytes || *info.memory_bytes > 0, "memory is absent or positive");
    check(!info.cache_line_bytes || (*info.cache_line_bytes > 0 &&
                                     (*info.cache_line_bytes & (*info.cache_line_bytes - 1)) == 0),
          "cache line is absent or a power of two");

    std::size_t cores_by_type = 0;
    bool every_type_counted = true;
    for (const auto& core : info.core_types) {
        every_type_counted = every_type_counted && !core.name.empty() && core.physical_cpus.has_value();
        cores_by_type += core.physical_cpus.value_or(0);
    }
    if (!info.core_types.empty() && every_type_counted && info.physical_cpus) {
        check(cores_by_type == *info.physical_cpus, "core types add up to the physical CPU count");
    }
}

void test_detect_macos_fields() {
#if defined(__APPLE__)
    // On macOS every field is supported, so none may be missing.
    const aether::SystemInfo info = aether::detect_system_info();
    check(info.os_family == "macOS", "os_family is macOS");
    check(info.os_version && info.os_build && info.kernel_release, "OS version, build and kernel");
    check(info.cpu_model.has_value(), "CPU model detected");
    check(info.logical_cpus && info.physical_cpus, "CPU counts detected");
    check(info.memory_bytes && *info.memory_bytes >= (std::uint64_t{1} << 30),
          "memory detected (at least 1 GiB)");
    check(info.cache_line_bytes.has_value(), "cache line size detected");
#if defined(__aarch64__)
    check(info.process_translated == false, "native arm64 build is not translated");
    check(!info.core_types.empty(), "Apple Silicon reports its core types");
#endif
#endif
}

void test_build_info() {
    const aether::BuildInfo build = aether::build_info();
    check(!build.compiler_id.empty() && build.compiler_id != "unknown", "compiler identified");
    check(!build.compiler_version.empty(), "compiler version present");
    check(build.cxx_standard == "C++20", "built as C++20 (CMAKE_CXX_STANDARD 20)");
    check(!build.build_type.empty() && build.build_type != "unknown",
          "build type injected by CMake");
#ifdef NDEBUG
    check(!build.assertions_enabled, "NDEBUG build reports assertions disabled");
#else
    check(build.assertions_enabled, "non-NDEBUG build reports assertions enabled");
#endif
}

void test_experiment_ids() {
    std::set<std::string> seen;
    bool all_valid = true;
    for (int i = 0; i < 10'000; ++i) {
        const std::string id = aether::make_experiment_id();
        all_valid = all_valid && aether::is_valid_experiment_id(id);
        seen.insert(id);
    }
    check(all_valid, "every id is a well-formed UUID v4");
    check(seen.size() == 10'000, "10,000 ids are all distinct");

    check(aether::is_valid_experiment_id("123e4567-e89b-42d3-a456-426614174000"), "valid id accepted");
    for (const char* bad : {"", "123e4567-e89b-12d3-a456-426614174000",   // version 1
                            "123e4567-e89b-42d3-c456-426614174000",       // wrong variant
                            "123E4567-E89B-42D3-A456-426614174000",       // uppercase
                            "123e4567e89b42d3a456426614174000",           // no dashes
                            "123e4567-e89b-42d3-a456-42661417400g"}) {    // non-hex
        check(!aether::is_valid_experiment_id(bad), std::string("rejects \"") + bad + "\"");
    }
}

}  // namespace

int main() {
    constexpr aether::test::TestCase kTests[] = {
        {"system: common fields and consistency", test_detect_common_fields},
        {"system: macOS fields all detected", test_detect_macos_fields},
        {"build: compiler, standard, configuration", test_build_info},
        {"ids: UUID v4 format and uniqueness", test_experiment_ids},
    };
    return aether::test::run_tests(kTests);
}
