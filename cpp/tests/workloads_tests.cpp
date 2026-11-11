// Tests for the named-workload runner and the aether_run_benchmark CLI parser.

#include <cstddef>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include "aether/experiment_id.hpp"
#include "aether/workloads.hpp"
#include "test_harness.hpp"

namespace {

using aether::test::check;
using aether::test::check_throws;

std::vector<std::string_view> valid_args() {
    return {"--workload", "vector_add_parallel", "--size",          "4096", "--threads",
            "2",          "--warmup-runs",       "1",               "--measured-runs",
            "3",          "--output",            "/tmp/out.json"};
}

// valid_args() with the value after `flag` replaced.
std::vector<std::string_view> with_value(std::string_view flag, std::string_view value) {
    auto args = valid_args();
    for (std::size_t i = 0; i + 1 < args.size(); i += 2) {
        if (args[i] == flag) {
            args[i + 1] = value;
        }
    }
    return args;
}

void test_workload_names_round_trip() {
    for (const auto workload : {aether::Workload::vector_add, aether::Workload::vector_add_parallel,
                                aether::Workload::row_sums_row_major,
                                aether::Workload::row_sums_column_major}) {
        check(aether::parse_workload(aether::workload_name(workload)) == workload,
              "name -> enum -> name round trip");
    }
    for (const char* bad : {"", "VECTOR_ADD", "vector_add ", "rm -rf /", "../vector_add"}) {
        check(!aether::parse_workload(bad).has_value(), std::string("rejects '") + bad + "'");
    }
}

void test_parse_valid_arguments() {
    const auto args = valid_args();
    const auto parsed = aether::parse_run_benchmark_args(args);
    check(parsed.spec.workload == aether::Workload::vector_add_parallel, "workload");
    check(parsed.spec.size == 4096 && parsed.spec.threads == 2, "size and threads");
    check(parsed.config.warmup_runs == 1 && parsed.config.measured_runs == 3, "run counts");
    check(parsed.config.min_warmup_ms == 100.0 && parsed.config.min_sample_ms == 1.0,
          "time-based defaults kept");
    check(parsed.output_path == "/tmp/out.json", "output path");
}

void test_parse_rejects_malformed_arguments() {
    const auto rejects = [](std::vector<std::string_view> args, const char* why) {
        check_throws<std::invalid_argument>([&] { (void)aether::parse_run_benchmark_args(args); },
                                            why);
    };
    rejects({}, "no arguments");
    auto missing = valid_args();
    missing.resize(missing.size() - 2);
    rejects(missing, "missing --output");
    auto duplicate = valid_args();
    duplicate.insert(duplicate.end(), {"--size", "8"});
    rejects(duplicate, "flag given twice");
    auto dangling = valid_args();
    dangling.push_back("--size");
    rejects(dangling, "flag without value");
    auto unknown = valid_args();
    unknown.insert(unknown.end(), {"--exec", "/bin/sh"});
    rejects(unknown, "unknown flag");
    rejects(with_value("--workload", "fork_bomb"), "unknown workload");
    for (const char* number : {"-1", "+4", " 4", "4 ", "4096x", "0x10", "1e3", "",
                               "99999999999999999999999"}) {
        rejects(with_value("--size", number), "non-decimal or overflowing number");
    }
    rejects(with_value("--output", ""), "empty output path");
}

void test_limits_enforced() {
    const auto rejects = [](std::vector<std::string_view> args, const char* why) {
        check_throws<std::invalid_argument>([&] { (void)aether::parse_run_benchmark_args(args); },
                                            why);
    };
    rejects(with_value("--size", "0"), "size 0");
    rejects(with_value("--size", "33554433"), "vector above 2^25 elements");
    rejects(with_value("--threads", "0"), "zero threads");
    rejects(with_value("--threads", "257"), "too many threads");
    rejects(with_value("--measured-runs", "0"), "zero measured runs");
    rejects(with_value("--measured-runs", "1001"), "too many measured runs");
    rejects(with_value("--warmup-runs", "1001"), "too many warm-up runs");

    auto matrix = with_value("--workload", "row_sums_row_major");
    for (std::size_t i = 0; i + 1 < matrix.size(); i += 2) {
        if (matrix[i] == "--threads") matrix[i + 1] = "1";
        if (matrix[i] == "--size") matrix[i + 1] = "8193";
    }
    rejects(matrix, "matrix above 8192 x 8192");
    rejects(with_value("--workload", "vector_add"), "threads = 2 on a single-threaded workload");
}

void test_run_every_workload() {
    const aether::BenchmarkConfig config{
        .warmup_runs = 1, .measured_runs = 3, .min_warmup_ms = 0.0, .min_sample_ms = 0.0};
    struct Case {
        aether::WorkloadSpec spec;
        std::size_t expected_input_size;
    };
    const Case cases[] = {
        {{aether::Workload::vector_add, 1000, 1}, 1000},
        {{aether::Workload::vector_add_parallel, 1001, 3}, 1001},  // uneven split
        {{aether::Workload::row_sums_row_major, 37, 1}, 37 * 37},
        {{aether::Workload::row_sums_column_major, 37, 1}, 37 * 37},
    };
    for (const Case& c : cases) {
        const auto record = aether::run_workload(c.spec, config);
        const std::string name(aether::workload_name(c.spec.workload));
        check(record.metadata.workload == name, name + ": workload name recorded");
        check(record.metadata.input_size == c.expected_input_size, name + ": input size recorded");
        check(record.samples_ms.size() == 3 && record.config.measured_runs == 3,
              name + ": one sample per measured run");
        check(aether::is_valid_experiment_id(record.experiment_id), name + ": experiment id");
    }
    const auto parallel = aether::run_workload({aether::Workload::vector_add_parallel, 1001, 3}, config);
    check(parallel.metadata.thread_count >= 1 && parallel.metadata.thread_count <= 3,
          "threads actually used are recorded");

    check_throws<std::invalid_argument>(
        [&] { (void)aether::run_workload({aether::Workload::vector_add, 0, 1}, config); },
        "run_workload validates its input too");
}

}  // namespace

int main() {
    constexpr aether::test::TestCase kTests[] = {
        {"workloads: name allowlist", test_workload_names_round_trip},
        {"cli: valid arguments", test_parse_valid_arguments},
        {"cli: malformed arguments rejected", test_parse_rejects_malformed_arguments},
        {"cli: limits enforced", test_limits_enforced},
        {"workloads: every workload runs and verifies", test_run_every_workload},
    };
    return aether::test::run_tests(kTests);
}
