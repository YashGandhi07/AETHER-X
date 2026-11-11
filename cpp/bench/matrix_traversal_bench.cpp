// Row-major vs column-major traversal of the same matrix (row sums).

#include <cstddef>
#include <cstdint>
#include <exception>
#include <format>
#include <iostream>
#include <vector>

#include "aether/benchmark.hpp"
#include "aether/deterministic_data.hpp"
#include "aether/matrix.hpp"
#include "aether/traversal.hpp"

#ifndef AETHER_BUILD_TYPE
#define AETHER_BUILD_TYPE "unknown"
#endif

namespace {

// Square n x n matrices: 256 KiB and 4 MiB fit in L2 (16 MiB on the M5
// performance cluster); 64 MiB spills to DRAM. 4097 breaks the power-of-two
// row stride of 4096, which otherwise maps every element of a column onto a
// small set of cache sets.
constexpr std::size_t kSizes[] = {256, 1024, 4096, 4097};
constexpr aether::BenchmarkConfig kConfig{
    .warmup_runs = 3, .measured_runs = 15, .min_warmup_ms = 100.0, .min_sample_ms = 1.0};
constexpr std::uint64_t kSeed = 0x2D2;

double gigabytes_per_second(std::size_t bytes, double ms) {
    return ms > 0.0 ? static_cast<double>(bytes) / (ms * 1e6) : 0.0;
}

// Returns true if both traversals verified and matched bit for bit.
bool run_case(std::size_t n) {
    const aether::Matrix m(n, n, aether::make_deterministic_floats(n * n, kSeed));
    std::vector<float> row_major_sums(n);
    std::vector<float> column_major_sums(n);

    const auto row_major = aether::run_benchmark(
        [&] {
            aether::row_sums_row_major(m, row_major_sums);
            aether::escape(row_major_sums.data());
        },
        kConfig);
    const auto column_major = aether::run_benchmark(
        [&] {
            aether::row_sums_column_major(m, column_major_sums);
            aether::escape(column_major_sums.data());
        },
        kConfig);

    const bool identical = row_major_sums == column_major_sums;
    const bool verified = !aether::find_first_bad_row_sum(m, row_major_sums).has_value() &&
                          !aether::find_first_bad_row_sum(m, column_major_sums).has_value();

    const std::size_t bytes = n * n * sizeof(float);
    std::cout << std::format("{:>6} {:>8.2f} {:>11.4f} {:>11.4f} {:>7.2f}x {:>8.1f} {:>8.1f}  {:>9}  {}\n",
                             n, static_cast<double>(bytes) / (1024.0 * 1024.0),
                             row_major.median_ms, column_major.median_ms,
                             column_major.median_ms / row_major.median_ms,
                             gigabytes_per_second(bytes, row_major.median_ms),
                             gigabytes_per_second(bytes, column_major.median_ms),
                             identical ? "yes" : "NO", verified ? "PASS" : "FAIL");
    return identical && verified;
}

}  // namespace

int main() {
    try {
        std::cout << std::format(
            "AETHER X matrix traversal benchmark: row sums of an n x n float matrix\n"
            "(build: {}, samples: {}, warm-up: >= {} runs and >= {} ms, min sample: {} ms)\n",
            AETHER_BUILD_TYPE, kConfig.measured_runs, kConfig.warmup_runs, kConfig.min_warmup_ms,
            kConfig.min_sample_ms);
        std::cout << std::format("{:>6} {:>8} {:>11} {:>11} {:>8} {:>8} {:>8}  {:>9}  {}\n", "n",
                                 "MiB", "row_ms", "col_ms", "col/row", "row_GB/s", "col_GB/s",
                                 "identical", "verify");

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
