// Sequential vs std::thread-parallel vector_add across sizes and thread counts.

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <exception>
#include <format>
#include <iostream>
#include <thread>
#include <vector>

#include "aether/benchmark.hpp"
#include "aether/deterministic_data.hpp"
#include "aether/parallel.hpp"
#include "aether/vector_add.hpp"

#ifndef AETHER_BUILD_TYPE
#define AETHER_BUILD_TYPE "unknown"
#endif

namespace {

// 48 KiB to 192 MiB working sets; the last size is deliberately uneven so the
// partition hands out chunks of different lengths.
constexpr std::size_t kSizes[] = {std::size_t{1} << 12, std::size_t{1} << 16,
                                  std::size_t{1} << 20, std::size_t{1} << 24,
                                  (std::size_t{1} << 24) + 3};
constexpr aether::BenchmarkConfig kConfig{
    .warmup_runs = 3, .measured_runs = 15, .min_warmup_ms = 100.0, .min_sample_ms = 1.0};
constexpr std::uint64_t kSeedA = 0xA23;
constexpr std::uint64_t kSeedB = 0xB23;

// 1, 2, 4, ... below the hardware thread count, then the hardware count itself.
std::vector<std::size_t> thread_counts_to_test(std::size_t hardware) {
    std::vector<std::size_t> counts;
    for (std::size_t t = 1; t < hardware; t *= 2) {
        counts.push_back(t);
    }
    counts.push_back(hardware);
    return counts;
}

double gigabytes_per_second(std::size_t n, double ms) {
    return ms > 0.0 ? 3.0 * static_cast<double>(n) * sizeof(float) / (ms * 1e6) : 0.0;
}

// Returns true if every parallel result matched the sequential one exactly.
bool run_size(std::size_t n, const std::vector<std::size_t>& thread_counts) {
    const std::vector<float> a = aether::make_deterministic_floats(n, kSeedA);
    const std::vector<float> b = aether::make_deterministic_floats(n, kSeedB);
    std::vector<float> expected(n);
    std::vector<float> out(n);

    const auto sequential = aether::run_benchmark(
        [&] {
            aether::vector_add(a, b, expected);
            aether::escape(expected.data());
        },
        kConfig);
    bool all_passed = !aether::find_first_mismatch(a, b, expected).has_value();

    std::cout << std::format("\n{} elements ({:.2f} MiB working set)\n", n,
                             3.0 * static_cast<double>(n) * sizeof(float) / (1024.0 * 1024.0));
    std::cout << std::format("  {:<12} {:>7} {:>11} {:>8} {:>10} {:>8}  {}\n", "variant",
                             "threads", "median_ms", "speedup", "efficiency", "GB/s", "verify");
    std::cout << std::format("  {:<12} {:>7} {:>11.4f} {:>7.2f}x {:>9.0f}% {:>8.1f}  {}\n",
                             "sequential", 1, sequential.median_ms, 1.0, 100.0,
                             gigabytes_per_second(n, sequential.median_ms),
                             all_passed ? "PASS" : "FAIL");

    const auto report = [&](const char* label, std::size_t requested, std::size_t min_per_thread) {
        std::size_t used = 0;
        std::fill(out.begin(), out.end(), 0.0f);
        const auto result = aether::run_benchmark(
            [&] {
                used = aether::vector_add_parallel(a, b, out, requested, min_per_thread);
                aether::escape(out.data());
            },
            kConfig);
        // Every element goes through the same float addition, so the parallel
        // result must equal the sequential one bit for bit.
        const bool passed = out == expected;
        all_passed = all_passed && passed;
        const double speedup = sequential.median_ms / result.median_ms;
        std::cout << std::format("  {:<12} {:>7} {:>11.4f} {:>7.2f}x {:>9.0f}% {:>8.1f}  {}\n",
                                 label, used, result.median_ms, speedup,
                                 100.0 * speedup / static_cast<double>(used),
                                 gigabytes_per_second(n, result.median_ms),
                                 passed ? "PASS" : "FAIL");
    };

    // min_elements_per_thread = 1 forces exactly the requested thread count,
    // exposing thread start-up overhead on small inputs.
    for (const std::size_t threads : thread_counts) {
        report("forced", threads, 1);
    }
    report("default", thread_counts.back(), aether::kDefaultMinElementsPerThread);
    return all_passed;
}

}  // namespace

int main() {
    try {
        const unsigned reported = std::thread::hardware_concurrency();
        const std::size_t hardware = reported > 0 ? reported : 1;
        std::cout << std::format(
            "AETHER X parallel vector_add benchmark (build: {}, hardware threads: {}, samples: {}, "
            "warm-up: >= {} runs and >= {} ms, min sample: {} ms)\n",
            AETHER_BUILD_TYPE, hardware, kConfig.measured_runs, kConfig.warmup_runs,
            kConfig.min_warmup_ms, kConfig.min_sample_ms);
        std::cout << std::format("'default' = vector_add_parallel(hardware threads, "
                                 "min_elements_per_thread = {})\n",
                                 aether::kDefaultMinElementsPerThread);

        const std::vector<std::size_t> thread_counts = thread_counts_to_test(hardware);
        bool all_passed = true;
        for (const std::size_t n : kSizes) {
            all_passed = run_size(n, thread_counts) && all_passed;
        }
        return all_passed ? 0 : 1;
    } catch (const std::exception& error) {
        std::cerr << "error: " << error.what() << '\n';
        return 1;
    }
}
