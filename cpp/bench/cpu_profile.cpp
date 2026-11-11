// Profiles the AETHER X CPU kernels and exports every sample plus summary
// statistics to JSON. Usage: aether_cpu_profile <output.json>
//
// The workloads themselves live in aether/workloads (shared with
// aether_run_benchmark); this program only chooses the fixed profile set.

#include <cstddef>
#include <exception>
#include <format>
#include <iostream>
#include <string>
#include <utility>
#include <vector>

#include "aether/benchmark.hpp"
#include "aether/profiling.hpp"
#include "aether/system_info.hpp"
#include "aether/workloads.hpp"

namespace {

// More samples than the exploratory benchmarks so p95 rests on real data:
// with 30 samples it interpolates between the 28th and 29th smallest.
constexpr aether::BenchmarkConfig kConfig{
    .warmup_runs = 3, .measured_runs = 30, .min_warmup_ms = 100.0, .min_sample_ms = 1.0};

void print(const aether::ProfileRecord& r) {
    const auto& s = r.statistics;
    std::cout << std::format("{:<22} {:>10} {:>3} {:>11.6f} {:>11.6f} {:>11.6f} {:>11.6f} "
                             "{:>11.6f} {:>7.2f}%\n",
                             r.metadata.workload, r.metadata.input_size, r.metadata.thread_count,
                             s.min, s.median, s.mean, s.p95, s.max,
                             s.stddev ? 100.0 * *s.stddev / s.mean : 0.0);
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 2) {
        std::cerr << "usage: " << (argc > 0 ? argv[0] : "aether_cpu_profile")
                  << " <output.json>\n";
        return 2;
    }
    try {
        const aether::SystemInfo system = aether::detect_system_info();
        const aether::BuildInfo build = aether::build_info();
        if (system.process_translated.value_or(false)) {
            std::cerr << "warning: running under Rosetta 2 translation; timings will not "
                         "reflect native performance\n";
        }
        // Thread counts come from the detected logical CPUs (null -> 1 thread).
        const std::size_t hardware = system.logical_cpus.value_or(1);
        std::cout << std::format(
            "AETHER X CPU profile: {} ({} logical CPUs), {} {} {}, {} {} {}, {} samples per workload\n",
            system.cpu_model.value_or("unknown CPU"), hardware, system.os_family,
            system.os_version.value_or("?"), system.architecture, build.compiler_id,
            build.compiler_version, build.build_type, kConfig.measured_runs);
        std::cout << std::format("{:<22} {:>10} {:>3} {:>11} {:>11} {:>11} {:>11} {:>11} {:>8}\n",
                                 "workload", "elements", "thr", "min_ms", "median_ms", "mean_ms",
                                 "p95_ms", "max_ms", "cv");

        using aether::Workload;
        std::vector<aether::WorkloadSpec> specs;
        for (const std::size_t n : {std::size_t{1} << 12, std::size_t{1} << 18, std::size_t{1} << 24}) {
            specs.push_back({.workload = Workload::vector_add, .size = n});
        }
        for (const std::size_t threads : {std::size_t{1}, std::size_t{2}, std::size_t{4}, hardware}) {
            specs.push_back({.workload = Workload::vector_add_parallel, .size = std::size_t{1} << 20,
                             .threads = threads});
        }
        specs.push_back({.workload = Workload::row_sums_row_major, .size = 4096});
        specs.push_back({.workload = Workload::row_sums_column_major, .size = 4096});

        std::vector<aether::ProfileRecord> records;
        for (const aether::WorkloadSpec& spec : specs) {
            records.push_back(aether::run_workload(spec, kConfig));
            print(records.back());
        }

        const std::string output_path = argv[1];
        const aether::ProfileReport report = aether::make_profile_report(std::move(records));
        aether::write_json_file(output_path, report);
        std::cout << std::format("\nwrote run {} ({} records) to {}\n", report.run_id,
                                 report.records.size(), output_path);
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "error: " << error.what() << '\n';
        return 1;
    }
}
