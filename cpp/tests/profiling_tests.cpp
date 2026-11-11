// Tests for statistics, metadata validation and JSON export in aether/profiling.

#include <chrono>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "aether/benchmark.hpp"
#include "aether/experiment_id.hpp"
#include "aether/profiling.hpp"
#include "json_writer.hpp"  // internal header; tests get src/ on their include path
#include "test_harness.hpp"

namespace {

using aether::test::check;
using aether::test::check_throws;

bool near(double actual, double expected, double tolerance = 1e-12) {
    return std::abs(actual - expected) <= tolerance;
}

aether::BenchmarkResult result_with_samples(std::vector<double> samples) {
    aether::BenchmarkResult result = aether::summarize(samples);
    result.iterations_per_sample = 4;
    return result;
}

aether::BenchmarkMetadata sample_metadata() {
    return {.workload = "vector_add",
            .input_size = 4096,
            .thread_count = 2,
            .timestamp_utc = "2026-01-02T03:04:05Z"};
}

// Fixed report contents so JSON assertions are deterministic.
aether::ProfileReport sample_report(std::vector<aether::ProfileRecord> records) {
    aether::ProfileReport report;
    report.run_id = "123e4567-e89b-42d3-a456-426614174000";
    report.generated_at_utc = "2026-01-02T03:04:06Z";
    report.system = {.os_family = "macOS",
                     .os_version = "26.6.2",
                     .os_build = "25G83",
                     .kernel_release = "25.6.0",
                     .architecture = "arm64",
                     .process_translated = false,
                     .cpu_model = "Test CPU",
                     .logical_cpus = 10,
                     .physical_cpus = 10,
                     .core_types = {{"Super", 4}, {"Efficiency", 6}},
                     .cache_line_bytes = 128,
                     .memory_bytes = 17'179'869'184ULL};
    report.build = {.compiler_id = "AppleClang",
                    .compiler_version = "21.0.0",
                    .cxx_standard = "C++20",
                    .build_type = "Release",
                    .cmake_cxx_flags = "-O3 -DNDEBUG",
                    .assertions_enabled = false};
    report.records = std::move(records);
    return report;
}

bool contains(const std::string& text, const std::string& fragment) {
    return text.find(fragment) != std::string::npos;
}

// ---------- statistics ----------

void test_percentile_type7() {
    // Reference values match numpy.percentile(..., method="linear").
    const std::vector<double> four{4.0, 1.0, 3.0, 2.0};  // unsorted on purpose
    check(near(aether::percentile(four, 0.95), 3.85), "p95 of 1..4 is 3.85");
    check(near(aether::percentile(four, 0.5), 2.5), "p50 of 1..4 is 2.5");
    check(aether::percentile(four, 0.0) == 1.0 && aether::percentile(four, 1.0) == 4.0,
          "p0 is min, p100 is max");

    std::vector<double> one_to_twenty;
    for (int i = 1; i <= 20; ++i) {
        one_to_twenty.push_back(i);
    }
    check(near(aether::percentile(one_to_twenty, 0.95), 19.05), "p95 of 1..20 is 19.05");
    check(aether::percentile(std::vector<double>{7.0}, 0.95) == 7.0, "single value");

    const std::vector<double> bad_value{1.0, std::numeric_limits<double>::quiet_NaN()};
    check_throws<std::invalid_argument>([] { (void)aether::percentile({}, 0.5); },
                                        "empty input throws");
    check_throws<std::invalid_argument>([&] { (void)aether::percentile(bad_value, 0.5); },
                                        "NaN input throws");
    check_throws<std::invalid_argument>([&] { (void)aether::percentile(four, 1.5); },
                                        "p > 1 throws");
    check_throws<std::invalid_argument>(
        [&] { (void)aether::percentile(four, std::numeric_limits<double>::quiet_NaN()); },
        "NaN p throws");
}

void test_compute_statistics() {
    // Textbook data set: mean 5, sample variance 32/7.
    const std::vector<double> data{2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0};
    const auto s = aether::compute_statistics(data);
    check(s.count == 8 && s.min == 2.0 && s.max == 9.0, "count, min, max");
    check(near(s.mean, 5.0), "mean");
    check(near(s.median, 4.5), "median averages the middle pair");
    check(s.stddev.has_value() && near(*s.stddev, std::sqrt(32.0 / 7.0)),
          "sample stddev uses n - 1");
    check(near(s.p95, 7.0 + 0.65 * 2.0), "p95 interpolates between 7 and 9");
    check(s.median == aether::median(data), "median agrees with benchmark.hpp median()");

    const auto single = aether::compute_statistics(std::vector<double>{3.5});
    check(!single.stddev.has_value(), "stddev undefined for one sample");
    check(single.mean == 3.5 && single.median == 3.5 && single.p95 == 3.5,
          "single sample statistics");

    const auto constant = aether::compute_statistics(std::vector<double>(10, 2.0));
    check(constant.stddev == 0.0, "constant samples have zero spread");

    check_throws<std::invalid_argument>([] { (void)aether::compute_statistics({}); },
                                        "empty samples throw");
    const std::vector<double> infinite{1.0, std::numeric_limits<double>::infinity()};
    check_throws<std::invalid_argument>([&] { (void)aether::compute_statistics(infinite); },
                                        "infinite sample throws");
}

void test_statistics_numerical_stability() {
    // Large offset with small spread: the naive E[x^2] - E[x]^2 formula loses
    // every significant digit here; the two-pass method must not.
    const double offset = 1e9;
    const std::vector<double> data{offset + 4, offset + 7, offset + 13, offset + 16};
    const auto s = aether::compute_statistics(data);
    check(near(s.mean, offset + 10.0, 1e-6), "mean with large offset");
    check(s.stddev.has_value() && near(*s.stddev, std::sqrt(30.0), 1e-6),
          "stddev with large offset (variance 30)");
}

// ---------- records and metadata ----------

void test_make_profile_record() {
    const aether::BenchmarkConfig config{.warmup_runs = 2, .measured_runs = 3};
    auto result = result_with_samples({3.0, 1.0, 2.0});
    result.warmup_runs_executed = 57;
    const auto record = aether::make_profile_record(sample_metadata(), config, result);
    check(record.metadata.workload == "vector_add" && record.metadata.thread_count == 2,
          "metadata carried over");
    check(record.samples_ms == std::vector<double>{3.0, 1.0, 2.0}, "samples kept in run order");
    check(record.iterations_per_sample == 4 && record.config.warmup_runs == 2,
          "config and iterations carried over");
    check(record.warmup_runs_executed == 57, "actual warm-up count carried over");
    check(record.statistics.median == 2.0 && record.statistics.count == 3, "statistics computed");
    check(aether::is_valid_experiment_id(record.experiment_id), "record gets a UUID v4");
    check(aether::make_profile_record(sample_metadata(), config, result).experiment_id !=
              record.experiment_id,
          "every record gets a distinct id");

    auto unnamed = sample_metadata();
    unnamed.workload.clear();
    check_throws<std::invalid_argument>(
        [&] { (void)aether::make_profile_record(unnamed, config, result_with_samples({1.0})); },
        "empty workload name throws");

    auto no_threads = sample_metadata();
    no_threads.thread_count = 0;
    check_throws<std::invalid_argument>(
        [&] { (void)aether::make_profile_record(no_threads, config, result_with_samples({1.0})); },
        "zero thread count throws");

    auto bad_time = sample_metadata();
    bad_time.timestamp_utc = "yesterday";
    check_throws<std::invalid_argument>(
        [&] { (void)aether::make_profile_record(bad_time, config, result_with_samples({1.0})); },
        "free-form timestamp throws");

    aether::BenchmarkResult negative = result_with_samples({1.0});
    negative.samples_ms = {1.0, -0.5};
    check_throws<std::invalid_argument>(
        [&] { (void)aether::make_profile_record(sample_metadata(), config, negative); },
        "negative sample throws");

    aether::BenchmarkResult empty;
    check_throws<std::invalid_argument>(
        [&] { (void)aether::make_profile_record(sample_metadata(), config, empty); },
        "no samples throws");
}

void test_timestamp_format() {
    check(aether::is_utc_timestamp(aether::utc_timestamp_now()), "now() has the required layout");
    check(aether::is_utc_timestamp("2026-11-11T02:57:18Z"), "valid layout accepted");
    for (const char* bad : {"", "2026-11-11 02:57:18Z", "2026-11-11T02:57:18", "2026-11-11T02:57:18+00:00",
                            "2026-1-11T02:57:18Z", "2026-11-11T02:57:18.5Z"}) {
        check(!aether::is_utc_timestamp(bad), std::string("rejects \"") + bad + "\"");
    }
}

// ---------- JSON ----------

void test_json_required_fields() {
    const aether::BenchmarkConfig config{.warmup_runs = 3, .measured_runs = 2};
    auto result = result_with_samples({0.1 + 0.2, 0.25});
    result.warmup_runs_executed = 12;
    const auto record = aether::make_profile_record(sample_metadata(), config, result);
    const std::string json = aether::to_json(sample_report({record}));

    for (const std::string& fragment : std::vector<std::string>{
             // report
             R"("schema": "aether.cpu_profile.v2")", R"("run_id": "123e4567-e89b-42d3-a456-426614174000")",
             R"("generated_at_utc": "2026-01-02T03:04:06Z")", R"("time_unit": "ms")",
             // environment.system
             R"("os_family": "macOS")", R"("os_version": "26.6.2")", R"("os_build": "25G83")",
             R"("kernel_release": "25.6.0")", R"("architecture": "arm64")",
             R"("process_translated": false)", R"("cpu_model": "Test CPU")", R"("logical_cpus": 10)",
             R"("physical_cpus": 10)", R"("name": "Super")", R"("name": "Efficiency")",
             R"("cache_line_bytes": 128)", R"("memory_bytes": 17179869184)",
             // environment.build
             R"("compiler_id": "AppleClang")", R"("compiler_version": "21.0.0")",
             R"("cxx_standard": "C++20")", R"("build_type": "Release")",
             R"("cmake_cxx_flags": "-O3 -DNDEBUG")", R"("assertions_enabled": false)",
             // record
             R"("experiment_id": ")" + record.experiment_id + "\"", R"("workload": "vector_add")",
             R"("input_size": 4096)", R"("thread_count": 2)", R"("timestamp_utc": "2026-01-02T03:04:05Z")",
             R"("warmup_runs": 3)", R"("measured_runs": 2)", R"("warmup_runs_executed": 12)",
             R"("iterations_per_sample": 4)", R"("count": 2)", R"("p95": )", R"("stddev": )"}) {
        check(contains(json, fragment), "contains " + fragment);
    }
    // Shortest round-trip formatting keeps every bit of the measured double.
    check(contains(json, R"("samples": [0.30000000000000004, 0.25])"),
          "samples serialized with exact round-trip precision, in run order");
}

void test_json_unavailable_values_are_null() {
    // Only the always-known fields set: everything optional must become null,
    // never 0, "" or a guess.
    aether::ProfileReport report = sample_report({});
    report.system = {.os_family = "unknown", .architecture = "unknown"};
    report.build.cmake_cxx_flags.reset();
    const std::string json = aether::to_json(report);

    for (const char* field : {"os_version", "os_build", "kernel_release", "process_translated",
                              "cpu_model", "logical_cpus", "physical_cpus", "cache_line_bytes",
                              "memory_bytes", "cmake_cxx_flags"}) {
        check(contains(json, std::string("\"") + field + "\": null"),
              std::string(field) + " exported as null");
    }
    check(contains(json, R"("core_types": [])"), "no core types gives an empty array");
    check(contains(json, R"("results": [])"), "empty result list is valid JSON");

    const auto single =
        aether::make_profile_record(sample_metadata(), {}, result_with_samples({1.5}));
    check(contains(aether::to_json(sample_report({single})), R"("stddev": null)"),
          "undefined stddev exported as null");
}

void test_json_value_formatting() {
    aether::ProfileReport report = sample_report({});
    report.system.memory_bytes = std::numeric_limits<std::uint64_t>::max();
    report.system.process_translated = true;
    const std::string json = aether::to_json(report);
    check(contains(json, R"("memory_bytes": 18446744073709551615)"),
          "64-bit integers written exactly (not via double)");
    check(contains(json, R"("process_translated": true)"), "booleans as JSON literals");

    auto metadata = sample_metadata();
    metadata.workload = "a\"b\\c\nd\te\x01";
    const auto record = aether::make_profile_record(metadata, {}, result_with_samples({1.0}));
    check(contains(aether::to_json(sample_report({record})),
                   R"("workload": "a\"b\\c\nd\te\u0001")"),
          "quotes, backslashes and control characters escaped per RFC 8259");
}

void test_json_writer_structure() {
    aether::detail::JsonWriter json;
    json.begin_object();
    json.field("a", 1u);
    json.key("empty_object");
    json.begin_object();
    json.end_object();
    json.key("list");
    json.begin_array();
    json.value("x");
    json.null();
    json.end_array();
    json.end_object();
    check(json.take() == "{\n  \"a\": 1,\n  \"empty_object\": {},\n  \"list\": [\n    \"x\",\n    null\n  ]\n}\n",
          "exact layout: commas, indentation, empty containers");

    check_throws<std::logic_error>(
        [] {
            aether::detail::JsonWriter w;
            w.begin_object();
            w.value("no key");
        },
        "value without key inside object throws");
    check_throws<std::logic_error>(
        [] {
            aether::detail::JsonWriter w;
            w.begin_object();
            w.end_array();
        },
        "mismatched close throws");
    check_throws<std::logic_error>(
        [] {
            aether::detail::JsonWriter w;
            w.begin_array();
            (void)w.take();
        },
        "incomplete document throws");
}

void test_environment_json() {
    aether::ProfileReport report = sample_report({});
    const std::string json =
        aether::environment_to_json(report.system, report.build, "2026-01-02T03:04:06Z");
    check(contains(json, R"("schema": "aether.system_info.v1")"), "system info schema id");
    check(contains(json, R"("generated_at_utc": "2026-01-02T03:04:06Z")"), "timestamp");
    check(contains(json, R"("cpu_model": "Test CPU")") && contains(json, R"("compiler_id": "AppleClang")"),
          "same system and build objects as profile reports");
    check(!contains(json, "\"results\"") && !contains(json, "\"run_id\""),
          "no benchmark results in a system info document");

    report.system = {.os_family = "unknown", .architecture = "unknown"};
    check(contains(aether::environment_to_json(report.system, report.build, "t"),
                   R"("cpu_model": null)"),
          "undetected fields stay null");
}

void test_write_json_file() {
    namespace fs = std::filesystem;
    const fs::path dir = fs::temp_directory_path() /
                         ("aether_profiling_test_" +
                          std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
    fs::create_directories(dir);
    const fs::path path = dir / "profile.json";

    const auto report = sample_report(
        {aether::make_profile_record(sample_metadata(), {}, result_with_samples({1.0, 2.0}))});
    aether::write_json_file(path, report);

    std::ifstream file(path, std::ios::binary);
    std::stringstream contents;
    contents << file.rdbuf();
    check(contents.str() == aether::to_json(report), "file contents equal to_json()");
    check(!fs::exists(dir / "profile.json.tmp"), "temporary file removed after rename");

    check_throws<std::runtime_error>(
        [&] { aether::write_json_file(dir / "missing_dir" / "x.json", report); },
        "unwritable path throws");
    fs::remove_all(dir);
}

}  // namespace

int main() {
    constexpr aether::test::TestCase kTests[] = {
        {"stats: percentile (type 7)", test_percentile_type7},
        {"stats: compute_statistics", test_compute_statistics},
        {"stats: numerical stability", test_statistics_numerical_stability},
        {"record: make_profile_record validation", test_make_profile_record},
        {"record: timestamp format", test_timestamp_format},
        {"json: required fields (schema v2)", test_json_required_fields},
        {"json: unavailable values are null", test_json_unavailable_values_are_null},
        {"json: value formatting and escaping", test_json_value_formatting},
        {"json: writer structure and misuse", test_json_writer_structure},
        {"json: environment-only document", test_environment_json},
        {"json: atomic file write", test_write_json_file},
    };
    return aether::test::run_tests(kTests);
}
