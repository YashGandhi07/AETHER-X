#include "aether/profiling.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <format>
#include <fstream>
#include <stdexcept>
#include <system_error>
#include <utility>

#include "aether/experiment_id.hpp"
#include "json_writer.hpp"

namespace aether {
namespace {

void require_finite(std::span<const double> values, const char* caller) {
    if (values.empty()) {
        throw std::invalid_argument(std::string(caller) + ": no values");
    }
    for (const double value : values) {
        if (!std::isfinite(value)) {
            throw std::invalid_argument(std::string(caller) + ": values must be finite");
        }
    }
}

// Type 7 percentile over already-sorted, validated values.
double sorted_percentile(std::span<const double> sorted, double p) {
    const double h = static_cast<double>(sorted.size() - 1) * p;
    const auto lower = static_cast<std::size_t>(std::floor(h));
    const std::size_t upper = std::min(lower + 1, sorted.size() - 1);
    return sorted[lower] + (h - static_cast<double>(lower)) * (sorted[upper] - sorted[lower]);
}

void write_system(detail::JsonWriter& json, const SystemInfo& system) {
    json.begin_object();
    json.field("os_family", system.os_family);
    json.field("os_version", system.os_version);
    json.field("os_build", system.os_build);
    json.field("kernel_release", system.kernel_release);
    json.field("architecture", system.architecture);
    json.field("process_translated", system.process_translated);
    json.field("cpu_model", system.cpu_model);
    json.field("logical_cpus", system.logical_cpus);
    json.field("physical_cpus", system.physical_cpus);
    json.key("core_types");
    json.begin_array();
    for (const CpuCoreType& core : system.core_types) {
        json.begin_object();
        json.field("name", core.name);
        json.field("physical_cpus", core.physical_cpus);
        json.end_object();
    }
    json.end_array();
    json.field("cache_line_bytes", system.cache_line_bytes);
    json.field("memory_bytes", system.memory_bytes);
    json.end_object();
}

void write_build(detail::JsonWriter& json, const BuildInfo& build) {
    json.begin_object();
    json.field("compiler_id", build.compiler_id);
    json.field("compiler_version", build.compiler_version);
    json.field("cxx_standard", build.cxx_standard);
    json.field("build_type", build.build_type);
    json.field("cmake_cxx_flags", build.cmake_cxx_flags);
    json.field("assertions_enabled", build.assertions_enabled);
    json.end_object();
}

void write_record(detail::JsonWriter& json, const ProfileRecord& r) {
    const SampleStatistics& s = r.statistics;
    json.begin_object();
    json.field("experiment_id", r.experiment_id);
    json.field("workload", r.metadata.workload);
    json.field("input_size", r.metadata.input_size);
    json.field("thread_count", r.metadata.thread_count);
    json.field("timestamp_utc", r.metadata.timestamp_utc);

    json.key("config");
    json.begin_object();
    json.field("warmup_runs", r.config.warmup_runs);
    json.field("measured_runs", r.config.measured_runs);
    json.field("min_warmup_ms", r.config.min_warmup_ms);
    json.field("min_sample_ms", r.config.min_sample_ms);
    json.end_object();

    json.field("warmup_runs_executed", r.warmup_runs_executed);
    json.field("iterations_per_sample", r.iterations_per_sample);

    json.key("statistics");
    json.begin_object();
    json.field("count", s.count);
    json.field("min", s.min);
    json.field("max", s.max);
    json.field("mean", s.mean);
    json.field("median", s.median);
    json.field("stddev", s.stddev);
    json.field("p95", s.p95);
    json.end_object();

    json.key("samples");
    json.number_array(r.samples_ms);
    json.end_object();
}

}  // namespace

double percentile(std::span<const double> values, double p) {
    require_finite(values, "percentile");
    if (!(p >= 0.0 && p <= 1.0)) {
        throw std::invalid_argument("percentile: p must be in [0, 1]");
    }
    std::vector<double> sorted(values.begin(), values.end());
    std::sort(sorted.begin(), sorted.end());
    return sorted_percentile(sorted, p);
}

SampleStatistics compute_statistics(std::span<const double> samples) {
    require_finite(samples, "compute_statistics");
    std::vector<double> sorted(samples.begin(), samples.end());
    std::sort(sorted.begin(), sorted.end());

    SampleStatistics stats;
    stats.count = sorted.size();
    stats.min = sorted.front();
    stats.max = sorted.back();
    stats.median = sorted_percentile(sorted, 0.5);
    stats.p95 = sorted_percentile(sorted, 0.95);

    // Two-pass mean/variance: summing squared deviations from the mean avoids
    // the catastrophic cancellation of the one-pass E[x^2] - E[x]^2 formula.
    // Summing the sorted values (smallest first) also reduces rounding error.
    double sum = 0.0;
    for (const double x : sorted) {
        sum += x;
    }
    stats.mean = sum / static_cast<double>(stats.count);
    if (stats.count > 1) {
        double squared_deviations = 0.0;
        for (const double x : sorted) {
            squared_deviations += (x - stats.mean) * (x - stats.mean);
        }
        stats.stddev = std::sqrt(squared_deviations / static_cast<double>(stats.count - 1));
    }
    return stats;
}

ProfileRecord make_profile_record(BenchmarkMetadata metadata, const BenchmarkConfig& config,
                                  const BenchmarkResult& result) {
    if (metadata.workload.empty()) {
        throw std::invalid_argument("make_profile_record: workload name must not be empty");
    }
    if (metadata.thread_count == 0) {
        throw std::invalid_argument("make_profile_record: thread_count must be at least 1");
    }
    if (!is_utc_timestamp(metadata.timestamp_utc)) {
        throw std::invalid_argument("make_profile_record: timestamp must be YYYY-MM-DDTHH:MM:SSZ");
    }
    for (const double sample : result.samples_ms) {
        if (!(sample >= 0.0)) {  // also rejects NaN
            throw std::invalid_argument("make_profile_record: samples must be non-negative");
        }
    }
    ProfileRecord record;
    record.statistics = compute_statistics(result.samples_ms);  // validates non-empty/finite
    record.experiment_id = make_experiment_id();
    record.metadata = std::move(metadata);
    record.config = config;
    record.warmup_runs_executed = result.warmup_runs_executed;
    record.iterations_per_sample = result.iterations_per_sample;
    record.samples_ms = result.samples_ms;
    return record;
}

ProfileReport make_profile_report(std::vector<ProfileRecord> records) {
    return {.run_id = make_experiment_id(),
            .generated_at_utc = utc_timestamp_now(),
            .system = detect_system_info(),
            .build = build_info(),
            .records = std::move(records)};
}

std::string utc_timestamp_now() {
    const auto now = std::chrono::floor<std::chrono::seconds>(std::chrono::system_clock::now());
    return std::format("{:%FT%TZ}", now);
}

bool is_utc_timestamp(const std::string& text) noexcept {
    // Layout check: YYYY-MM-DDTHH:MM:SSZ (20 chars). Calendar validity is the
    // producer's job; this guards against free-form strings in the report.
    constexpr std::string_view kPattern = "dddd-dd-ddTdd:dd:ddZ";
    if (text.size() != kPattern.size()) {
        return false;
    }
    for (std::size_t i = 0; i < kPattern.size(); ++i) {
        const bool digit = text[i] >= '0' && text[i] <= '9';
        if (kPattern[i] == 'd' ? !digit : text[i] != kPattern[i]) {
            return false;
        }
    }
    return true;
}

std::string to_json(const ProfileReport& report) {
    detail::JsonWriter json;
    json.begin_object();
    json.field("schema", kProfileSchema);
    json.field("run_id", report.run_id);
    json.field("generated_at_utc", report.generated_at_utc);
    json.field("time_unit", "ms");

    json.key("environment");
    json.begin_object();
    json.key("system");
    write_system(json, report.system);
    json.key("build");
    write_build(json, report.build);
    json.end_object();

    json.key("results");
    json.begin_array();
    for (const ProfileRecord& record : report.records) {
        write_record(json, record);
    }
    json.end_array();
    json.end_object();
    return json.take();
}

std::string environment_to_json(const SystemInfo& system, const BuildInfo& build,
                                const std::string& generated_at_utc) {
    detail::JsonWriter json;
    json.begin_object();
    json.field("schema", kSystemInfoSchema);
    json.field("generated_at_utc", generated_at_utc);
    json.key("system");
    write_system(json, system);
    json.key("build");
    write_build(json, build);
    json.end_object();
    return json.take();
}

void write_json_file(const std::filesystem::path& path, const ProfileReport& report) {
    const std::string json = to_json(report);
    std::filesystem::path temporary = path;
    temporary += ".tmp";
    {
        std::ofstream file(temporary, std::ios::binary | std::ios::trunc);
        if (!file) {
            throw std::runtime_error("write_json_file: cannot open " + temporary.string());
        }
        file.write(json.data(), static_cast<std::streamsize>(json.size()));
        file.flush();
        if (!file) {
            throw std::runtime_error("write_json_file: failed writing " + temporary.string());
        }
    }
    std::error_code error;
    std::filesystem::rename(temporary, path, error);
    if (error) {
        std::error_code ignored;
        std::filesystem::remove(temporary, ignored);
        throw std::runtime_error("write_json_file: cannot replace " + path.string() + ": " +
                                 error.message());
    }
}

}  // namespace aether
