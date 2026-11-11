#pragma once

#include <atomic>
#include <chrono>
#include <concepts>
#include <cstddef>
#include <stdexcept>
#include <utility>
#include <vector>

namespace aether {

struct BenchmarkConfig {
    std::size_t warmup_runs = 3;
    std::size_t measured_runs = 15;
    // Warm-up also lasts at least this long, so the CPU has time to ramp up to
    // its steady clock frequency before any sample is taken.
    double min_warmup_ms = 100.0;
    // Each sample repeats the workload until the batch lasts at least this
    // long, so clock-tick quantization (~42 ns on macOS) stays negligible for
    // workloads that finish in well under a microsecond. 0 disables batching.
    double min_sample_ms = 1.0;
};

struct BenchmarkResult {
    std::vector<double> samples_ms;  // per-call time of each sample, in run order
    double median_ms = 0.0;
    double min_ms = 0.0;
    double max_ms = 0.0;
    std::size_t iterations_per_sample = 1;
};

// Median of `values`; the mean of the two middle elements for even sizes.
// Throws std::invalid_argument if `values` is empty.
double median(std::vector<double> values);

// Computes median/min/max over the samples. Throws if `samples_ms` is empty.
BenchmarkResult summarize(std::vector<double> samples_ms);

// Smallest non-zero step observed between consecutive steady_clock readings.
double measure_clock_resolution_ns();

// Makes the compiler assume the memory behind `pointer` is read at this point,
// so stores into it (e.g. a benchmark's output) cannot be discarded as dead or
// merged across iterations. Emits no instructions on GCC/Clang.
inline void escape(const void* pointer) noexcept {
#if defined(__GNUC__) || defined(__clang__)
    asm volatile("" : : "g"(pointer) : "memory");
#else
    static const void* volatile sink = nullptr;
    sink = pointer;
    std::atomic_signal_fence(std::memory_order_seq_cst);
#endif
}

// Times `workload` in three phases:
//   1. calibration: double the batch size until one batch >= min_sample_ms;
//   2. warm-up: at least warmup_runs batches and at least min_warmup_ms;
//   3. measurement: measured_runs batches, each reported as time per call.
template <typename Workload>
    requires std::invocable<Workload&>
BenchmarkResult run_benchmark(Workload&& workload, const BenchmarkConfig& config) {
    using Clock = std::chrono::steady_clock;
    static_assert(Clock::is_steady, "benchmark clock must be monotonic");

    if (config.measured_runs == 0) {
        throw std::invalid_argument("run_benchmark: measured_runs must be at least 1");
    }
    // Negated comparisons so NaN is rejected as well.
    if (!(config.min_warmup_ms >= 0.0) || !(config.min_sample_ms >= 0.0)) {
        throw std::invalid_argument("run_benchmark: time limits must be non-negative");
    }

    const auto elapsed_ms = [](Clock::time_point start, Clock::time_point stop) {
        return std::chrono::duration<double, std::milli>(stop - start).count();
    };
    const auto time_batch_ms = [&](std::size_t iterations) {
        const auto start = Clock::now();
        for (std::size_t i = 0; i < iterations; ++i) {
            workload();
        }
        return elapsed_ms(start, Clock::now());
    };

    // The cap stops runaway doubling if the workload is optimized to nothing.
    constexpr std::size_t kMaxIterations = std::size_t{1} << 30;
    std::size_t iterations = 1;
    if (config.min_sample_ms > 0.0) {
        while (time_batch_ms(iterations) < config.min_sample_ms && iterations < kMaxIterations) {
            iterations *= 2;
        }
    }

    const auto warmup_start = Clock::now();
    for (std::size_t run = 0; run < config.warmup_runs ||
                              elapsed_ms(warmup_start, Clock::now()) < config.min_warmup_ms;
         ++run) {
        time_batch_ms(iterations);
    }

    std::vector<double> samples_ms;
    samples_ms.reserve(config.measured_runs);
    for (std::size_t run = 0; run < config.measured_runs; ++run) {
        samples_ms.push_back(time_batch_ms(iterations) / static_cast<double>(iterations));
    }

    BenchmarkResult result = summarize(std::move(samples_ms));
    result.iterations_per_sample = iterations;
    return result;
}

}  // namespace aether
