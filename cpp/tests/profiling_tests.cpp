// Tests for statistics, metadata validation and JSON export in aether/profiling.

#include <chrono>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <limits>
#include <regex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

#include "aether/benchmark.hpp"
#include "aether/profiling.hpp"
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
            .compiler = "test-compiler 1.0",
            .build_type = "Release",
            .timestamp_utc = "2026-01-02T03:04:05Z"};
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
    const auto record =
        aether::make_profile_record(sample_metadata(), config, result_with_samples({3.0, 1.0, 2.0}));
    check(record.metadata.workload == "vector_add" && record.metadata.thread_count == 2,
          "metadata carried over");
    check(record.samples_ms == std::vector<double>{3.0, 1.0, 2.0}, "samples kept in run order");
    check(record.iterations_per_sample == 4 && record.config.warmup_runs == 2,
          "config and iterations carried over");
    check(record.statistics.median == 2.0 && record.statistics.count == 3, "statistics computed");

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

void test_environment_metadata() {
    check(!aether::compiler_description().empty(), "compiler description available");
    const std::string timestamp = aether::utc_timestamp_now();
    check(std::regex_match(timestamp, std::regex(R"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)")),
          "timestamp is ISO 8601 UTC with seconds");
}

// ---------- JSON ----------

void test_json_schema_and_values() {
    const aether::BenchmarkConfig config{.warmup_runs = 3, .measured_runs = 2};
    const std::vector<aether::ProfileRecord> records{aether::make_profile_record(
        sample_metadata(), config, result_with_samples({0.1 + 0.2, 0.25}))};
    const std::string json = aether::to_json(records, "2026-01-02T03:04:05Z");

    for (const char* key :
         {"\"schema\": \"aether.cpu_profile.v1\"", "\"generated_at_utc\": \"2026-01-02T03:04:05Z\"",
          "\"time_unit\": \"ms\"", "\"workload\": \"vector_add\"", "\"input_size\": 4096",
          "\"thread_count\": 2", "\"iterations_per_sample\": 4", "\"warmup_runs\": 3",
          "\"count\": 2", "\"p95\": ", "\"stddev\": "}) {
        check(json.find(key) != std::string::npos, std::string("contains ") + key);
    }
    // Shortest round-trip formatting keeps every bit of the measured double.
    check(json.find("\"samples\": [0.30000000000000004, 0.25]") != std::string::npos,
          "samples serialized with exact round-trip precision, in run order");

    const auto single = aether::make_profile_record(sample_metadata(), config,
                                                    result_with_samples({1.5}));
    check(aether::to_json(std::span(&single, 1), "t").find("\"stddev\": null") != std::string::npos,
          "undefined stddev exported as null");
    check(aether::to_json({}, "t").find("\"results\": []") != std::string::npos,
          "empty result list is valid JSON");
}

void test_json_string_escaping() {
    auto metadata = sample_metadata();
    metadata.workload = "a\"b\\c\nd\te\x01";
    const auto record = aether::make_profile_record(metadata, {}, result_with_samples({1.0}));
    const std::string json = aether::to_json(std::span(&record, 1), "t");
    check(json.find(R"("workload": "a\"b\\c\nd\te\u0001")") != std::string::npos,
          "quotes, backslashes and control characters escaped per RFC 8259");
}

void test_write_json_file() {
    namespace fs = std::filesystem;
    const fs::path dir = fs::temp_directory_path() /
                         ("aether_profiling_test_" +
                          std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
    fs::create_directories(dir);
    const fs::path path = dir / "profile.json";

    const auto record = aether::make_profile_record(sample_metadata(), {}, result_with_samples({1.0, 2.0}));
    aether::write_json_file(path, std::span(&record, 1), "2026-01-02T03:04:05Z");

    std::ifstream file(path, std::ios::binary);
    std::stringstream contents;
    contents << file.rdbuf();
    check(contents.str() == aether::to_json(std::span(&record, 1), "2026-01-02T03:04:05Z"),
          "file contents equal to_json()");
    check(!fs::exists(dir / "profile.json.tmp"), "temporary file removed after rename");

    check_throws<std::runtime_error>(
        [&] { aether::write_json_file(dir / "missing_dir" / "x.json", std::span(&record, 1), "t"); },
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
        {"record: compiler and timestamp", test_environment_metadata},
        {"json: schema and values", test_json_schema_and_values},
        {"json: string escaping", test_json_string_escaping},
        {"json: atomic file write", test_write_json_file},
    };
    return aether::test::run_tests(kTests);
}
