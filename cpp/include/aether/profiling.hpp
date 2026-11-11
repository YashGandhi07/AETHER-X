#pragma once

#include <cstddef>
#include <filesystem>
#include <optional>
#include <span>
#include <string>
#include <vector>

#include "aether/benchmark.hpp"

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

struct BenchmarkMetadata {
    std::string workload;          // e.g. "vector_add_parallel"
    std::size_t input_size = 0;    // elements processed per call
    std::size_t thread_count = 1;  // threads actually used, not requested
    std::string compiler;          // see compiler_description()
    std::string build_type;        // e.g. "Release"
    std::string timestamp_utc;     // ISO 8601, see utc_timestamp_now()
};

// One profiled benchmark: what ran, how it was measured, and every sample.
struct ProfileRecord {
    BenchmarkMetadata metadata;
    BenchmarkConfig config;
    std::size_t iterations_per_sample = 1;
    std::vector<double> samples_ms;  // per-call time, in run order
    SampleStatistics statistics;
};

// Bundles a run_benchmark result with its metadata and computes statistics.
// Throws std::invalid_argument if the workload name is empty, thread_count is
// 0, there are no samples, or any sample is negative or non-finite.
ProfileRecord make_profile_record(BenchmarkMetadata metadata, const BenchmarkConfig& config,
                                  const BenchmarkResult& result);

// Compiler name and version from predefined macros, e.g. "Apple clang 21.0.0".
std::string compiler_description();

// Current UTC time, second precision: "YYYY-MM-DDTHH:MM:SSZ".
std::string utc_timestamp_now();

inline constexpr const char* kProfileSchema = "aether.cpu_profile.v1";

// Serializes records as a single JSON document (schema kProfileSchema).
// Doubles use the shortest representation that round-trips exactly.
std::string to_json(std::span<const ProfileRecord> records, const std::string& generated_at_utc);

// Writes to_json() output to `path` via a temporary file and rename, so a
// crash never leaves a truncated report. Throws std::runtime_error on I/O failure.
void write_json_file(const std::filesystem::path& path, std::span<const ProfileRecord> records,
                     const std::string& generated_at_utc);

}  // namespace aether
