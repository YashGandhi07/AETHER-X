#include "aether/workloads.hpp"

#include <algorithm>
#include <array>
#include <charconv>
#include <cstdint>
#include <map>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "aether/deterministic_data.hpp"
#include "aether/matrix.hpp"
#include "aether/parallel.hpp"
#include "aether/traversal.hpp"
#include "aether/vector_add.hpp"

namespace aether {
namespace {

constexpr std::array<std::pair<Workload, std::string_view>, 4> kWorkloadNames{{
    {Workload::vector_add, "vector_add"},
    {Workload::vector_add_parallel, "vector_add_parallel"},
    {Workload::row_sums_row_major, "row_sums_row_major"},
    {Workload::row_sums_column_major, "row_sums_column_major"},
}};

// Fixed seeds: every run of a given spec benchmarks bit-identical inputs.
constexpr std::uint64_t kSeedA = 0xA24;
constexpr std::uint64_t kSeedB = 0xB24;
constexpr std::uint64_t kSeedMatrix = 0x2D4;

bool is_matrix_workload(Workload workload) noexcept {
    return workload == Workload::row_sums_row_major || workload == Workload::row_sums_column_major;
}

void require(bool condition, const std::string& message) {
    if (!condition) {
        throw std::invalid_argument(message);
    }
}

// Benchmarks `workload`, then requires `verified()` on the final output.
template <typename Fn, typename Verify>
ProfileRecord measure(std::string_view name, std::size_t input_size, std::size_t threads,
                      const BenchmarkConfig& config, Fn&& workload, Verify&& verified) {
    const BenchmarkResult result = run_benchmark(workload, config);
    if (!verified()) {
        throw std::runtime_error("verification failed for " + std::string(name));
    }
    return make_profile_record({.workload = std::string(name),
                                .input_size = input_size,
                                .thread_count = threads,
                                .timestamp_utc = utc_timestamp_now()},
                               config, result);
}

ProfileRecord run_vector(const WorkloadSpec& spec, const BenchmarkConfig& config) {
    const std::size_t n = spec.size;
    const auto a = make_deterministic_floats(n, kSeedA);
    const auto b = make_deterministic_floats(n, kSeedB);
    std::vector<float> out(n);
    const auto verified = [&] { return !find_first_mismatch(a, b, out).has_value(); };

    if (spec.workload == Workload::vector_add) {
        return measure(workload_name(spec.workload), n, 1, config,
                       [&] {
                           vector_add(a, b, out);
                           escape(out.data());
                       },
                       verified);
    }
    // min_elements_per_thread = 1 uses exactly the requested threads (capped
    // at hardware concurrency); record what was actually used.
    const std::size_t used = plan_thread_count(spec.threads, n, 1);
    return measure(workload_name(spec.workload), n, used, config,
                   [&] {
                       (void)vector_add_parallel(a, b, out, spec.threads, 1);
                       escape(out.data());
                   },
                   verified);
}

ProfileRecord run_matrix(const WorkloadSpec& spec, const BenchmarkConfig& config) {
    const std::size_t n = spec.size;
    const Matrix m(n, n, make_deterministic_floats(n * n, kSeedMatrix));
    std::vector<float> sums(n);
    const auto verified = [&] { return !find_first_bad_row_sum(m, sums).has_value(); };
    const bool row_major = spec.workload == Workload::row_sums_row_major;
    return measure(workload_name(spec.workload), n * n, 1, config,
                   [&] {
                       if (row_major) {
                           row_sums_row_major(m, sums);
                       } else {
                           row_sums_column_major(m, sums);
                       }
                       escape(sums.data());
                   },
                   verified);
}

// Strict unsigned decimal: digits only, no sign, no whitespace, no overflow.
std::size_t parse_count(std::string_view flag, std::string_view text) {
    std::size_t value = 0;
    const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), value);
    require(!text.empty() && text.front() != '-' && text.front() != '+' && error == std::errc{} &&
                end == text.data() + text.size(),
            std::string(flag) + " expects a non-negative integer, got '" + std::string(text) + "'");
    return value;
}

}  // namespace

std::string_view workload_name(Workload workload) noexcept {
    for (const auto& [value, name] : kWorkloadNames) {
        if (value == workload) {
            return name;
        }
    }
    return "unknown";
}

std::optional<Workload> parse_workload(std::string_view name) noexcept {
    for (const auto& [value, known] : kWorkloadNames) {
        if (known == name) {
            return value;
        }
    }
    return std::nullopt;
}

void validate_workload(const WorkloadSpec& spec, const BenchmarkConfig& config) {
    const std::string name(workload_name(spec.workload));
    require(spec.size >= 1, name + ": size must be at least 1");
    if (is_matrix_workload(spec.workload)) {
        require(spec.size <= kMaxMatrixDimension,
                name + ": matrix dimension must be <= " + std::to_string(kMaxMatrixDimension));
    } else {
        require(spec.size <= kMaxVectorElements,
                name + ": size must be <= " + std::to_string(kMaxVectorElements));
    }
    require(spec.threads >= 1 && spec.threads <= kMaxThreads,
            name + ": threads must be in [1, " + std::to_string(kMaxThreads) + "]");
    require(spec.workload == Workload::vector_add_parallel || spec.threads == 1,
            name + ": single-threaded workload requires threads = 1");
    require(config.warmup_runs <= kMaxWarmupRuns,
            "warmup runs must be <= " + std::to_string(kMaxWarmupRuns));
    require(config.measured_runs >= 1 && config.measured_runs <= kMaxMeasuredRuns,
            "measured runs must be in [1, " + std::to_string(kMaxMeasuredRuns) + "]");
}

ProfileRecord run_workload(const WorkloadSpec& spec, const BenchmarkConfig& config) {
    validate_workload(spec, config);
    return is_matrix_workload(spec.workload) ? run_matrix(spec, config) : run_vector(spec, config);
}

RunBenchmarkArgs parse_run_benchmark_args(std::span<const std::string_view> args) {
    static constexpr std::array<std::string_view, 6> kFlags{
        "--workload", "--size", "--threads", "--warmup-runs", "--measured-runs", "--output"};
    std::map<std::string_view, std::string_view> values;
    for (std::size_t i = 0; i < args.size(); i += 2) {
        const std::string_view flag = args[i];
        require(std::find(kFlags.begin(), kFlags.end(), flag) != kFlags.end(),
                "unknown argument '" + std::string(flag) + "'");
        require(i + 1 < args.size(), std::string(flag) + " requires a value");
        require(values.emplace(flag, args[i + 1]).second,
                std::string(flag) + " given more than once");
    }
    for (const std::string_view flag : kFlags) {
        require(values.contains(flag), "missing required argument " + std::string(flag));
    }

    RunBenchmarkArgs parsed;
    const auto workload = parse_workload(values["--workload"]);
    require(workload.has_value(), "unknown workload '" + std::string(values["--workload"]) + "'");
    parsed.spec = {.workload = *workload,
                   .size = parse_count("--size", values["--size"]),
                   .threads = parse_count("--threads", values["--threads"])};
    // Time-based warm-up and batching keep the defaults established in Section 2.1.
    parsed.config = {.warmup_runs = parse_count("--warmup-runs", values["--warmup-runs"]),
                     .measured_runs = parse_count("--measured-runs", values["--measured-runs"]),
                     .min_warmup_ms = 100.0,
                     .min_sample_ms = 1.0};
    parsed.output_path = std::string(values["--output"]);
    require(!parsed.output_path.empty(), "--output must not be empty");
    validate_workload(parsed.spec, parsed.config);
    return parsed;
}

}  // namespace aether
