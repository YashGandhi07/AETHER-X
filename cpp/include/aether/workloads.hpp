#pragma once

#include <cstddef>
#include <optional>
#include <span>
#include <string>
#include <string_view>

#include "aether/benchmark.hpp"
#include "aether/profiling.hpp"

namespace aether {

// The benchmark workloads the engine can run by name. This list is the
// allowlist for every external caller (CLI tools and, through them, the API).
enum class Workload {
    vector_add,             // out = a + b, one thread
    vector_add_parallel,    // out = a + b, std::thread chunks
    row_sums_row_major,     // n x n matrix row sums, contiguous reads
    row_sums_column_major,  // same sums, strided reads
};

std::string_view workload_name(Workload workload) noexcept;
std::optional<Workload> parse_workload(std::string_view name) noexcept;

// Engine-side hard limits: a second line of defence behind the (stricter)
// API limits, so a direct CLI call cannot exhaust memory either.
inline constexpr std::size_t kMaxVectorElements = std::size_t{1} << 25;  // 3 arrays: 384 MiB
inline constexpr std::size_t kMaxMatrixDimension = 8192;                 // 256 MiB
inline constexpr std::size_t kMaxThreads = 256;
inline constexpr std::size_t kMaxWarmupRuns = 1000;
inline constexpr std::size_t kMaxMeasuredRuns = 1000;

struct WorkloadSpec {
    Workload workload = Workload::vector_add;
    // Elements for vector workloads; matrix dimension n (n x n) for row sums.
    std::size_t size = 0;
    // Requested threads; only vector_add_parallel accepts more than 1.
    std::size_t threads = 1;
};

// Throws std::invalid_argument if the spec or config is outside the limits above.
void validate_workload(const WorkloadSpec& spec, const BenchmarkConfig& config);

// Builds deterministic inputs, benchmarks the workload, verifies the final
// output against an independent reference, and returns the profiled record.
// Throws std::invalid_argument for invalid input and std::runtime_error if
// verification fails (so a wrong kernel never yields a report).
ProfileRecord run_workload(const WorkloadSpec& spec, const BenchmarkConfig& config);

struct RunBenchmarkArgs {
    WorkloadSpec spec;
    BenchmarkConfig config;
    std::string output_path;
};

// Parses the aether_run_benchmark command line (without argv[0]):
//   --workload NAME --size N --threads T --warmup-runs W --measured-runs R --output PATH
// Every flag is required exactly once; numbers must be plain unsigned decimals.
// Throws std::invalid_argument with a precise message on any violation.
RunBenchmarkArgs parse_run_benchmark_args(std::span<const std::string_view> args);

}  // namespace aether
