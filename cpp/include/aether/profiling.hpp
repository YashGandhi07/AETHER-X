#pragma once

#include <cstddef>
#include <filesystem>
#include <optional>
#include <span>
#include <string>
#include <vector>

#include "aether/benchmark.hpp"
#include "aether/system_info.hpp"

namespace aether {

// Descriptive statistics over a set of timing samples.
struct SampleStatistics {
    std::size_t count = 0;
    double min = 0.0;
    double max = 0.0;
    double mean = 0.0;
    double median = 0.0;
    // Sample standard deviation (Bessel's n - 1 correction). Undefined for a
    // single sample, so it is empty rather than a misleading 0.
    std::optional<double> stddev;
    double p95 = 0.0;
};

// Percentile p in [0, 1] using linear interpolation between closest ranks
// (Hyndman & Fan "type 7"; the default in NumPy, R, Excel PERCENTILE.INC):
//   h = (n - 1) * p,  result = x[floor(h)] + (h - floor(h)) * (x[floor(h) + 1] - x[floor(h)])
// over the sorted values. Gives the conventional median at p = 0.5. Throws
// std::invalid_argument for empty or non-finite input or p outside [0, 1].
double percentile(std::span<const double> values, double p);

// Throws std::invalid_argument for empty input or any non-finite value.
SampleStatistics compute_statistics(std::span<const double> samples);

// What ran. How it was built and where is recorded once per report (see
// ProfileReport), since every record in a run shares the same binary and host.
struct BenchmarkMetadata {
    std::string workload;          // e.g. "vector_add_parallel"
    std::size_t input_size = 0;    // elements processed per call
    std::size_t thread_count = 1;  // threads actually used, not requested
    std::string timestamp_utc;     // ISO 8601, see utc_timestamp_now()
};

// One profiled benchmark: identity, what ran, how it was measured, every sample.
struct ProfileRecord {
    std::string experiment_id;  // UUID v4, unique per record
    BenchmarkMetadata metadata;
    BenchmarkConfig config;
    std::size_t warmup_runs_executed = 0;
    std::size_t iterations_per_sample = 1;
    std::vector<double> samples_ms;  // per-call time, in run order
    SampleStatistics statistics;
};

// A complete profiling run: one environment, many records.
struct ProfileReport {
    std::string run_id;  // UUID v4
    std::string generated_at_utc;
    SystemInfo system;
    BuildInfo build;
    std::vector<ProfileRecord> records;
};

// Bundles a run_benchmark result with its metadata, assigns a fresh experiment
// id and computes statistics. Throws std::invalid_argument if the workload name
// is empty, thread_count is 0, the timestamp is not ISO 8601 UTC, there are no
// samples, or any sample is negative or non-finite.
ProfileRecord make_profile_record(BenchmarkMetadata metadata, const BenchmarkConfig& config,
                                  const BenchmarkResult& result);

// Detects the system and build environment and wraps `records` in a report
// with a fresh run id and the current time.
ProfileReport make_profile_report(std::vector<ProfileRecord> records);

// Current UTC time, second precision: "YYYY-MM-DDTHH:MM:SSZ".
std::string utc_timestamp_now();

// True for exactly the "YYYY-MM-DDTHH:MM:SSZ" layout produced above.
bool is_utc_timestamp(const std::string& text) noexcept;

inline constexpr const char* kProfileSchema = "aether.cpu_profile.v2";

// Serializes a report as one JSON document (schema kProfileSchema). Undetected
// environment fields are written as null; doubles round-trip exactly.
std::string to_json(const ProfileReport& report);

// Writes to_json() output to `path` via a temporary file and rename, so a
// crash never leaves a truncated report. Throws std::runtime_error on I/O failure.
void write_json_file(const std::filesystem::path& path, const ProfileReport& report);

}  // namespace aether
