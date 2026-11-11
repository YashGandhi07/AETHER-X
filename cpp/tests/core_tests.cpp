// Correctness tests for the aether_core kernels and benchmark utilities.

#include <algorithm>
#include <cstddef>
#include <limits>
#include <span>
#include <stdexcept>
#include <thread>
#include <vector>

#include "aether/benchmark.hpp"
#include "aether/deterministic_data.hpp"
#include "aether/matrix.hpp"
#include "aether/parallel.hpp"
#include "aether/traversal.hpp"
#include "aether/vector_add.hpp"
#include "test_harness.hpp"

namespace {

using aether::test::check;
using aether::test::check_throws;

// ---------- vector_add ----------

void test_adds_elementwise() {
    const std::vector<float> a{1.0f, -2.5f, 0.0f, 1e6f};
    const std::vector<float> b{2.0f, 2.5f, -0.0f, 1.0f};
    std::vector<float> out(a.size());
    aether::vector_add(a, b, out);
    check(out == std::vector<float>{3.0f, 0.0f, 0.0f, 1000001.0f}, "sums match");
}

void test_empty_arrays() {
    const std::vector<float> a, b;
    std::vector<float> out;
    aether::vector_add(a, b, out);  // must not throw
    check(out.empty(), "output stays empty");
    check(!aether::find_first_mismatch(a, b, out).has_value(), "empty input verifies");
}

void test_mismatched_sizes_throw() {
    const std::vector<float> three(3, 1.0f), two(2, 1.0f);
    std::vector<float> out(3, 7.0f);
    check_throws<std::invalid_argument>([&] { aether::vector_add(three, two, out); },
                                        "a/b size mismatch throws");
    check_throws<std::invalid_argument>([&] { aether::vector_add(two, two, out); },
                                        "output size mismatch throws");
    check(out == std::vector<float>(3, 7.0f), "output untouched when validation fails");
    check_throws<std::invalid_argument>(
        [&] { (void)aether::find_first_mismatch(three, two, out); },
        "verification size mismatch throws");
}

void test_in_place_alias_allowed() {
    std::vector<float> a{1.0f, 2.0f, 3.0f};
    const std::vector<float> b{10.0f, 20.0f, 30.0f};
    aether::vector_add(a, b, a);
    check(a == std::vector<float>{11.0f, 22.0f, 33.0f}, "a += b in place");
}

void test_partial_overlap_throws() {
    std::vector<float> buffer(5, 1.0f);
    const std::vector<float> b(4, 1.0f);
    const std::span<const float> a(buffer.data(), 4);
    const std::span<float> shifted_out(buffer.data() + 1, 4);
    check_throws<std::invalid_argument>([&] { aether::vector_add(a, b, shifted_out); },
                                        "offset overlap with input is rejected");
}

// ---------- verification ----------

void test_mismatch_detection() {
    const std::vector<float> a{1.0f, 2.0f, 3.0f};
    const std::vector<float> b{1.0f, 1.0f, 1.0f};
    std::vector<float> out{2.0f, 3.0f, 4.0f};
    check(!aether::find_first_mismatch(a, b, out).has_value(), "correct output verifies");

    out[1] = 3.0f + 1e-7f;  // within tolerance (float rounding noise)
    check(!aether::find_first_mismatch(a, b, out).has_value(), "tiny error tolerated");

    out[1] = 3.001f;
    check(aether::find_first_mismatch(a, b, out) == 1u, "real error reported at its index");

    out[1] = 3.0f;
    out[2] = std::numeric_limits<float>::quiet_NaN();
    check(aether::find_first_mismatch(a, b, out) == 2u, "NaN is a mismatch");

    check_throws<std::invalid_argument>(
        [&] { (void)aether::find_first_mismatch(a, b, out, {.absolute = -1.0}); },
        "negative tolerance throws");
}

// ---------- deterministic data ----------

void test_deterministic_data() {
    // Published SplitMix64 reference value for seed 0; pins the algorithm.
    check(aether::SplitMix64(0).next() == 0xE220A8397B1DCDAFULL, "SplitMix64 reference output");

    const auto first = aether::make_deterministic_floats(1000, 42);
    check(first == aether::make_deterministic_floats(1000, 42), "same seed, same values");
    check(first != aether::make_deterministic_floats(1000, 43), "different seed, different values");

    bool in_range = true;
    for (const float value : first) {
        in_range = in_range && value >= -1.0f && value < 1.0f;
    }
    check(in_range, "values lie in [-1, 1)");
    check(aether::make_deterministic_floats(0, 42).empty(), "zero count gives empty vector");
}

// ---------- timing utilities ----------

void test_median() {
    check(aether::median({5.0}) == 5.0, "single value");
    check(aether::median({3.0, 1.0, 2.0}) == 2.0, "odd count, unsorted");
    check(aether::median({4.0, 1.0, 3.0, 2.0}) == 2.5, "even count averages middle pair");
    check_throws<std::invalid_argument>([] { (void)aether::median({}); }, "empty input throws");
}

void test_run_benchmark() {
    // With time limits disabled the run counts are exact.
    int calls = 0;
    const auto result = aether::run_benchmark(
        [&] { ++calls; },
        {.warmup_runs = 2, .measured_runs = 10, .min_warmup_ms = 0.0, .min_sample_ms = 0.0});
    check(calls == 12, "warmup + measured runs executed");
    check(result.samples_ms.size() == 10, "one sample per measured run");
    check(result.iterations_per_sample == 1, "no batching when min_sample_ms is 0");
    check(result.min_ms >= 0.0 && result.min_ms <= result.median_ms &&
              result.median_ms <= result.max_ms,
          "0 <= min <= median <= max");

    check_throws<std::invalid_argument>(
        [] { (void)aether::run_benchmark([] {}, {.measured_runs = 0}); },
        "zero measured runs throws");
    check_throws<std::invalid_argument>(
        [] { (void)aether::run_benchmark([] {}, {.min_sample_ms = -1.0}); },
        "negative time limit throws");
}

void test_run_benchmark_batches_short_workloads() {
    // A call takes nanoseconds, far below the 0.2 ms minimum, so it must batch.
    int calls = 0;
    const auto result = aether::run_benchmark(
        [&] {
            ++calls;
            aether::escape(&calls);
        },
        {.warmup_runs = 1, .measured_runs = 10, .min_warmup_ms = 0.0, .min_sample_ms = 0.2});
    check(result.iterations_per_sample > 1, "short workload is batched");
    check(result.samples_ms.size() == 10, "still one sample per measured run");
    check(result.median_ms < 0.2, "samples are per call, not per batch");
}

void test_clock_resolution() {
    const double resolution_ns = aether::measure_clock_resolution_ns();
    check(resolution_ns > 0.0 && resolution_ns < 1e6, "resolution is positive and below 1 ms");
}

// ---------- Matrix ----------

void test_matrix_layout_and_validation() {
    const aether::Matrix m(2, 3, {1.0f, 2.0f, 3.0f, 4.0f, 5.0f, 6.0f});
    check(m.rows() == 2 && m.cols() == 3, "dimensions");
    check(m(0, 2) == 3.0f && m(1, 0) == 4.0f, "row-major layout: (r, c) at r * cols + c");
    check(m.at(1, 2) == 6.0f, "checked access");
    check_throws<std::out_of_range>([&] { (void)m.at(2, 0); }, "row out of range throws");
    check_throws<std::out_of_range>([&] { (void)m.at(0, 3); }, "column out of range throws");

    const aether::Matrix zeros(3, 4);
    bool all_zero = true;
    for (const float value : zeros.values()) {
        all_zero = all_zero && value == 0.0f;
    }
    check(zeros.values().size() == 12 && all_zero, "default construction zero-fills");

    check_throws<std::invalid_argument>([] { aether::Matrix(2, 2, {1.0f, 2.0f, 3.0f}); },
                                        "wrong value count throws");
    check_throws<std::length_error>(
        [] { aether::Matrix(std::numeric_limits<std::size_t>::max(), 2); },
        "rows * cols overflow throws before allocating");
}

// ---------- row-major vs column-major traversal ----------

void test_row_sums_known_values() {
    const aether::Matrix m(2, 3, {1.0f, 2.0f, 3.0f, -4.0f, 0.5f, 10.0f});
    std::vector<float> by_rows(2), by_columns(2, 99.0f);  // stale values must be overwritten
    aether::row_sums_row_major(m, by_rows);
    aether::row_sums_column_major(m, by_columns);
    check(by_rows == std::vector<float>{6.0f, 6.5f}, "row-major sums");
    check(by_columns == std::vector<float>{6.0f, 6.5f}, "column-major sums");
}

void test_row_sums_empty_shapes() {
    std::vector<float> none;
    aether::row_sums_row_major(aether::Matrix(0, 0), none);
    aether::row_sums_column_major(aether::Matrix(0, 5), none);
    check(none.empty(), "zero rows give no sums");

    std::vector<float> by_rows(3, 1.0f), by_columns(3, 1.0f);
    const aether::Matrix no_columns(3, 0);
    aether::row_sums_row_major(no_columns, by_rows);
    aether::row_sums_column_major(no_columns, by_columns);
    check(by_rows == std::vector<float>(3, 0.0f) && by_columns == by_rows,
          "zero columns give zero sums");
    check(!aether::find_first_bad_row_sum(no_columns, by_rows).has_value(),
          "zero-column sums verify");
}

void test_traversals_bit_identical() {
    // Non-square, non-power-of-two shape exercises any edge handling.
    const std::size_t rows = 37, cols = 211;
    const aether::Matrix m(rows, cols, aether::make_deterministic_floats(rows * cols, 7));
    std::vector<float> by_rows(rows), by_columns(rows);
    aether::row_sums_row_major(m, by_rows);
    aether::row_sums_column_major(m, by_columns);
    check(by_rows == by_columns, "same additions in same order, so bitwise equal");
    check(!aether::find_first_bad_row_sum(m, by_rows).has_value(), "within error bound");
}

void test_row_sums_validation() {
    aether::Matrix m(3, 4);
    std::vector<float> too_short(2, 7.0f);
    check_throws<std::invalid_argument>([&] { aether::row_sums_row_major(m, too_short); },
                                        "row-major: wrong output size throws");
    check_throws<std::invalid_argument>([&] { aether::row_sums_column_major(m, too_short); },
                                        "column-major: wrong output size throws");
    check(too_short == std::vector<float>(2, 7.0f), "output untouched when validation fails");

    // Pointing the output at the matrix's own storage would corrupt the input.
    const std::span<float> inside = m.values().subspan(4, 3);
    check_throws<std::invalid_argument>([&] { aether::row_sums_row_major(m, inside); },
                                        "row-major: output inside matrix throws");
    check_throws<std::invalid_argument>([&] { aether::row_sums_column_major(m, inside); },
                                        "column-major: output inside matrix throws");
    check_throws<std::invalid_argument>(
        [&] { (void)aether::find_first_bad_row_sum(m, too_short); },
        "verifier: wrong size throws");
}

void test_row_sum_verifier() {
    // One long row: float rounding error accumulates over 100k additions, and
    // the bound must still accept the correctly computed float result.
    const std::size_t cols = 100'000;
    const aether::Matrix m(1, cols, aether::make_deterministic_floats(cols, 11));
    std::vector<float> sums(1);
    aether::row_sums_row_major(m, sums);
    check(!aether::find_first_bad_row_sum(m, sums).has_value(), "float result accepted");

    // For this row the rigorous bound is ~1.5 (actual error ~0.002), so an
    // error must exceed that to be provably wrong.
    const float correct = sums[0];
    sums[0] = correct + 5.0f;
    check(aether::find_first_bad_row_sum(m, sums) == 0u, "sum off by 5.0 rejected");
    sums[0] = std::numeric_limits<float>::quiet_NaN();
    check(aether::find_first_bad_row_sum(m, sums) == 0u, "NaN rejected");

    // A realistic off-by-one bug at benchmark row length: the last element of
    // the row is never added.
    const std::size_t bench_cols = 4096;
    const aether::Matrix row(1, bench_cols, aether::make_deterministic_floats(bench_cols, 13));
    std::vector<float> row_sum(1);
    aether::row_sums_row_major(row, row_sum);
    row_sum[0] -= row(0, bench_cols - 1);
    check(aether::find_first_bad_row_sum(row, row_sum) == 0u, "missing last element rejected");
}

// ---------- multithreading ----------

std::size_t hardware_threads() {
    const unsigned reported = std::thread::hardware_concurrency();
    return reported > 0 ? reported : 1;
}

void test_partition_range() {
    // Uneven split: 10 = 4 + 3 + 3, contiguous and in order.
    check(aether::partition_range(10, 3) ==
              std::vector<aether::IndexRange>{{0, 4}, {4, 7}, {7, 10}},
          "remainder goes to the first ranges");
    check(aether::partition_range(8, 4) ==
              std::vector<aether::IndexRange>{{0, 2}, {2, 4}, {4, 6}, {6, 8}},
          "even split");
    check(aether::partition_range(2, 4) ==
              std::vector<aether::IndexRange>{{0, 1}, {1, 2}, {2, 2}, {2, 2}},
          "more parts than elements gives empty tail ranges");
    check(aether::partition_range(0, 1) == std::vector<aether::IndexRange>{{0, 0}},
          "empty input");

    // Property check over many shapes: full coverage, no gaps, balanced.
    bool valid = true;
    for (std::size_t count : {1u, 7u, 64u, 1000u, 1001u}) {
        for (std::size_t parts = 1; parts <= 12; ++parts) {
            const auto ranges = aether::partition_range(count, parts);
            std::size_t expected_begin = 0, smallest = count, largest = 0;
            for (const auto& range : ranges) {
                valid = valid && range.begin == expected_begin && range.end >= range.begin;
                expected_begin = range.end;
                smallest = std::min(smallest, range.size());
                largest = std::max(largest, range.size());
            }
            valid = valid && ranges.size() == parts && expected_begin == count &&
                    largest - smallest <= 1;
        }
    }
    check(valid, "covers [0, count) exactly with sizes differing by <= 1");
    check_throws<std::invalid_argument>([] { (void)aether::partition_range(10, 0); },
                                        "zero parts throws");
}

void test_plan_thread_count() {
    const std::size_t hardware = hardware_threads();
    check(aether::plan_thread_count(1, 1'000'000, 1) == 1, "one thread when asked for one");
    check(aether::plan_thread_count(hardware * 4, 1'000'000, 1) == hardware,
          "capped at hardware concurrency (no oversubscription)");
    check(aether::plan_thread_count(8, 3, 1) == std::min<std::size_t>(3, hardware),
          "never more threads than elements");
    check(aether::plan_thread_count(8, 1000, 400) == std::min<std::size_t>(3, hardware),
          "one thread per min_elements_per_thread, rounded up");
    check(aether::plan_thread_count(8, 0, 1) == 1, "empty input still reports one thread");
    check_throws<std::invalid_argument>([] { (void)aether::plan_thread_count(0, 10, 1); },
                                        "zero requested threads throws");
    check_throws<std::invalid_argument>([] { (void)aether::plan_thread_count(2, 10, 0); },
                                        "zero min_elements_per_thread throws");
}

void test_parallel_matches_sequential() {
    // Uneven sizes (primes, sizes smaller than the thread count) and thread
    // counts beyond the hardware limit, which must be clamped.
    bool all_match = true;
    bool counts_valid = true;
    for (std::size_t n : {1u, 3u, 17u, 1000u, 10'007u}) {
        const auto a = aether::make_deterministic_floats(n, 21);
        const auto b = aether::make_deterministic_floats(n, 22);
        std::vector<float> expected(n);
        aether::vector_add(a, b, expected);
        for (std::size_t threads = 1; threads <= hardware_threads() + 3; ++threads) {
            std::vector<float> out(n, -1.0f);
            const std::size_t used = aether::vector_add_parallel(a, b, out, threads, 1);
            all_match = all_match && out == expected;
            counts_valid = counts_valid && used >= 1 && used <= std::min(threads, n) &&
                           used <= hardware_threads();
        }
    }
    check(all_match, "bit-identical to sequential for every size and thread count");
    check(counts_valid, "threads used never exceed request, elements or hardware");
}

void test_parallel_edge_cases() {
    const std::vector<float> empty_in;
    std::vector<float> empty_out;
    check(aether::vector_add_parallel(empty_in, empty_in, empty_out, 4, 1) == 1,
          "empty arrays: no worker threads started");

    const std::vector<float> three(3, 1.0f), two(2, 1.0f);
    std::vector<float> out(3, 7.0f);
    check_throws<std::invalid_argument>(
        [&] { (void)aether::vector_add_parallel(three, two, out, 2, 1); },
        "a/b size mismatch throws");
    check_throws<std::invalid_argument>(
        [&] { (void)aether::vector_add_parallel(two, two, out, 2, 1); },
        "output size mismatch throws");
    check_throws<std::invalid_argument>(
        [&] { (void)aether::vector_add_parallel(three, three, out, 0, 1); },
        "zero threads throws");
    check(out == std::vector<float>(3, 7.0f), "output untouched when validation fails");

    // Output shifted by half the array: each chunk's output overlaps another
    // thread's input chunk, a race only whole-array validation can catch.
    std::vector<float> buffer(12, 1.0f);
    const std::span<const float> first_half(buffer.data(), 8);
    const std::span<float> shifted(buffer.data() + 4, 8);
    const std::vector<float> ones(8, 1.0f);
    check_throws<std::invalid_argument>(
        [&] { (void)aether::vector_add_parallel(first_half, ones, shifted, 2, 1); },
        "cross-chunk overlap rejected before threads start");

    std::vector<float> in_place{1.0f, 2.0f, 3.0f, 4.0f, 5.0f};
    const std::vector<float> addend(5, 10.0f);
    (void)aether::vector_add_parallel(in_place, addend, in_place, 3, 1);
    check(in_place == std::vector<float>{11.0f, 12.0f, 13.0f, 14.0f, 15.0f},
          "exact in-place aliasing works across threads");
}

}  // namespace

int main() {
    constexpr aether::test::TestCase kTests[] = {
        {"vector_add: element-wise sums", test_adds_elementwise},
        {"vector_add: empty arrays", test_empty_arrays},
        {"vector_add: mismatched sizes", test_mismatched_sizes_throw},
        {"vector_add: in-place aliasing", test_in_place_alias_allowed},
        {"vector_add: partial overlap", test_partial_overlap_throws},
        {"verify: tolerance and NaN", test_mismatch_detection},
        {"data: deterministic generation", test_deterministic_data},
        {"timing: median", test_median},
        {"timing: run_benchmark", test_run_benchmark},
        {"timing: batching of short workloads", test_run_benchmark_batches_short_workloads},
        {"timing: clock resolution", test_clock_resolution},
        {"matrix: layout and validation", test_matrix_layout_and_validation},
        {"traversal: known row sums", test_row_sums_known_values},
        {"traversal: empty shapes", test_row_sums_empty_shapes},
        {"traversal: row/column bit-identical", test_traversals_bit_identical},
        {"traversal: input validation", test_row_sums_validation},
        {"traversal: error-bound verifier", test_row_sum_verifier},
        {"parallel: partition_range", test_partition_range},
        {"parallel: plan_thread_count", test_plan_thread_count},
        {"parallel: matches sequential", test_parallel_matches_sequential},
        {"parallel: edge cases and validation", test_parallel_edge_cases},
    };
    return aether::test::run_tests(kTests);
}
