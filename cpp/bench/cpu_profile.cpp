// Profiles the AETHER X CPU kernels and exports every sample plus summary
// statistics to JSON. Usage: aether_cpu_profile <output.json>

#include <cstddef>
#include <exception>
#include <format>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include "aether/benchmark.hpp"
#include "aether/deterministic_data.hpp"
#include "aether/matrix.hpp"
#include "aether/parallel.hpp"
#include "aether/profiling.hpp"
#include "aether/traversal.hpp"
#include "aether/vector_add.hpp"

#ifndef AETHER_BUILD_TYPE
#define AETHER_BUILD_TYPE "unknown"
#endif

namespace {

// More samples than the exploratory benchmarks so p95 rests on real data:
// with 30 samples it interpolates between the 28th and 29th smallest.
constexpr aether::BenchmarkConfig kConfig{
    .warmup_runs = 3, .measured_runs = 30, .min_warmup_ms = 100.0, .min_sample_ms = 1.0};

class Profiler {
public:
    Profiler() : compiler_(aether::compiler_description()) {}

    // Benchmarks `workload`, then requires `verified()` to hold on the final
    // output, so a broken kernel can never produce a plausible-looking report.
    template <typename Workload, typename Verify>
    void profile(std::string workload_name, std::size_t input_size, std::size_t thread_count,
                 Workload&& workload, Verify&& verified) {
        const aether::BenchmarkResult result = aether::run_benchmark(workload, kConfig);
        if (!verified()) {
            throw std::runtime_error("verification failed for " + workload_name);
        }
        records_.push_back(aether::make_profile_record(
            {.workload = std::move(workload_name),
             .input_size = input_size,
             .thread_count = thread_count,
             .compiler = compiler_,
             .build_type = AETHER_BUILD_TYPE,
             .timestamp_utc = aether::utc_timestamp_now()},
            kConfig, result));
        print(records_.back());
    }

    const std::vector<aether::ProfileRecord>& records() const noexcept { return records_; }

private:
    static void print(const aether::ProfileRecord& r) {
        const auto& s = r.statistics;
        std::cout << std::format("{:<22} {:>10} {:>3} {:>11.6f} {:>11.6f} {:>11.6f} {:>11.6f} "
                                 "{:>11.6f} {:>7.2f}%\n",
                                 r.metadata.workload, r.metadata.input_size,
                                 r.metadata.thread_count, s.min, s.median, s.mean, s.p95, s.max,
                                 s.stddev ? 100.0 * *s.stddev / s.mean : 0.0);
    }

    std::string compiler_;
    std::vector<aether::ProfileRecord> records_;
};

void profile_vector_add(Profiler& profiler, std::size_t n) {
    const auto a = aether::make_deterministic_floats(n, 0xA24);
    const auto b = aether::make_deterministic_floats(n, 0xB24);
    std::vector<float> out(n);
    profiler.profile(
        "vector_add", n, 1,
        [&] {
            aether::vector_add(a, b, out);
            aether::escape(out.data());
        },
        [&] { return !aether::find_first_mismatch(a, b, out).has_value(); });
}

void profile_vector_add_parallel(Profiler& profiler, std::size_t n, std::size_t threads) {
    const auto a = aether::make_deterministic_floats(n, 0xA24);
    const auto b = aether::make_deterministic_floats(n, 0xB24);
    std::vector<float> out(n);
    // Record the threads actually used (the request may be capped).
    const std::size_t used = aether::plan_thread_count(threads, n, 1);
    profiler.profile(
        "vector_add_parallel", n, used,
        [&] {
            (void)aether::vector_add_parallel(a, b, out, threads, 1);
            aether::escape(out.data());
        },
        [&] { return !aether::find_first_mismatch(a, b, out).has_value(); });
}

void profile_traversals(Profiler& profiler, std::size_t n) {
    const aether::Matrix m(n, n, aether::make_deterministic_floats(n * n, 0x2D4));
    std::vector<float> sums(n);
    const auto verified = [&] { return !aether::find_first_bad_row_sum(m, sums).has_value(); };
    profiler.profile(
        "row_sums_row_major", n * n, 1,
        [&] {
            aether::row_sums_row_major(m, sums);
            aether::escape(sums.data());
        },
        verified);
    profiler.profile(
        "row_sums_column_major", n * n, 1,
        [&] {
            aether::row_sums_column_major(m, sums);
            aether::escape(sums.data());
        },
        verified);
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 2) {
        std::cerr << "usage: " << (argc > 0 ? argv[0] : "aether_cpu_profile")
                  << " <output.json>\n";
        return 2;
    }
    try {
        const unsigned reported = std::thread::hardware_concurrency();
        const std::size_t hardware = reported > 0 ? reported : 1;
        std::cout << std::format("AETHER X CPU profile ({}, {}, {} samples per workload)\n",
                                 aether::compiler_description(), AETHER_BUILD_TYPE,
                                 kConfig.measured_runs);
        std::cout << std::format("{:<22} {:>10} {:>3} {:>11} {:>11} {:>11} {:>11} {:>11} {:>8}\n",
                                 "workload", "elements", "thr", "min_ms", "median_ms", "mean_ms",
                                 "p95_ms", "max_ms", "cv");

        Profiler profiler;
        for (const std::size_t n : {std::size_t{1} << 12, std::size_t{1} << 18, std::size_t{1} << 24}) {
            profile_vector_add(profiler, n);
        }
        for (const std::size_t threads : {std::size_t{1}, std::size_t{2}, std::size_t{4}, hardware}) {
            profile_vector_add_parallel(profiler, std::size_t{1} << 20, threads);
        }
        profile_traversals(profiler, 4096);

        const std::string output_path = argv[1];
        aether::write_json_file(output_path, profiler.records(), aether::utc_timestamp_now());
        std::cout << std::format("\nwrote {} records to {}\n", profiler.records().size(),
                                 output_path);
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "error: " << error.what() << '\n';
        return 1;
    }
}
