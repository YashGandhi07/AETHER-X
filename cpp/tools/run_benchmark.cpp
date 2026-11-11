// Runs ONE named workload with explicit parameters and writes an
// aether.cpu_profile.v2 report (one record).
//
//   aether_run_benchmark --workload NAME --size N --threads T
//                        --warmup-runs W --measured-runs R --output PATH
//
// Exit codes: 0 success, 1 runtime failure (including failed verification;
// no report is written), 2 invalid arguments.

#include <exception>
#include <iostream>
#include <stdexcept>
#include <string_view>
#include <vector>

#include "aether/profiling.hpp"
#include "aether/workloads.hpp"

int main(int argc, char** argv) {
    const std::vector<std::string_view> args(argv + (argc > 0 ? 1 : 0), argv + argc);
    aether::RunBenchmarkArgs parsed;
    try {
        parsed = aether::parse_run_benchmark_args(args);
    } catch (const std::invalid_argument& error) {
        std::cerr << "error: " << error.what() << "\nusage: aether_run_benchmark --workload NAME "
                  << "--size N --threads T --warmup-runs W --measured-runs R --output PATH\n";
        return 2;
    }
    try {
        aether::ProfileRecord record = aether::run_workload(parsed.spec, parsed.config);
        std::vector<aether::ProfileRecord> records;
        records.push_back(std::move(record));
        aether::write_json_file(parsed.output_path, aether::make_profile_report(std::move(records)));
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "error: " << error.what() << '\n';
        return 1;
    }
}
