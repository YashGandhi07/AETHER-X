// CPU benchmark for aether::vector_add across the cache hierarchy.

#include <cstddef>
#include <cstdint>
#include <exception>
#include <format>
#include <iostream>
#include <vector>

#include "aether/benchmark.hpp"
#include "aether/deterministic_data.hpp"
#include "aether/vector_add.hpp"

#ifndef AETHER_BUILD_TYPE
#define AETHER_BUILD_TYPE "unknown"
#endif

namespace {

// Working set is 3 arrays * 4 bytes * n: 48 KiB (L1), 3 MiB (L2), 192 MiB (DRAM).
constexpr std::size_t kSizes[] = {std::size_t{1} << 12, std::size_t{1} << 18, std::size_t{1} << 24};
constexpr aether::BenchmarkConfig kConfig{
    .warmup_runs = 3, .measured_runs = 15, .min_warmup_ms = 100.0, .min_sample_ms = 1.0};
constexpr std::uint64_t kSeedA = 0xA37E5;
constexpr std::uint64_t kSeedB = 0xB37E5;

// Returns true if every output element verified correctly.
bool run_case(std::size_t n) {
    const std::vector<float> a = aether::make_deterministic_floats(n, kSeedA);
    const std::vector<float> b = aether::make_deterministic_floats(n, kSeedB);
    std::vector<float> out(n);

    const aether::BenchmarkResult result = aether::run_benchmark(
        [&] {
            aether::vector_add(a, b, out);
            aether::escape(out.data());
        },
        kConfig);

    // Bytes moved per run: two reads and one write. Write-allocate traffic is
    // not counted, so this is the effective, not raw, bandwidth.
    const double bytes = 3.0 * static_cast<double>(n) * sizeof(float);
    const double gb_per_s = result.median_ms > 0.0 ? bytes / (result.median_ms * 1e6) : 0.0;

    const auto mismatch = aether::find_first_mismatch(a, b, out);
    std::cout << std::format("{:>10} {:>9.1f} {:>7} {:>12.6f} {:>12.6f} {:>12.6f} {:>9.1f}  {}\n",
                             n, bytes / 1024.0, result.iterations_per_sample, result.median_ms,
                             result.min_ms, result.max_ms, gb_per_s, mismatch ? "FAIL" : "PASS");
    if (mismatch) {
        const std::size_t i = *mismatch;
        std::cerr << std::format("  mismatch at index {}: {} + {} gave {}\n", i, a[i], b[i], out[i]);
    }
    return !mismatch;
}

}  // namespace

int main() {
    try {
        std::cout << std::format(
            "AETHER X vector_add CPU benchmark (build: {}, samples: {}, warm-up: >= {} runs and "
            ">= {} ms, min sample: {} ms)\n",
            AETHER_BUILD_TYPE, kConfig.measured_runs, kConfig.warmup_runs, kConfig.min_warmup_ms,
            kConfig.min_sample_ms);
        std::cout << std::format("steady_clock resolution (measured): {:.1f} ns\n",
                                 aether::measure_clock_resolution_ns());
        std::cout << std::format("{:>10} {:>9} {:>7} {:>12} {:>12} {:>12} {:>9}  {}\n", "elements",
                                 "KiB", "iters", "median_ms", "min_ms", "max_ms", "GB/s",
                                 "verify");

        bool all_passed = true;
        for (const std::size_t n : kSizes) {
            all_passed = run_case(n) && all_passed;
        }
        return all_passed ? 0 : 1;
    } catch (const std::exception& error) {
        std::cerr << "error: " << error.what() << '\n';
        return 1;
    }
}
