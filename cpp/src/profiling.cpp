#include "aether/profiling.hpp"

#include <algorithm>
#include <charconv>
#include <chrono>
#include <cmath>
#include <format>
#include <fstream>
#include <stdexcept>
#include <system_error>

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

// ---------- JSON output ----------

void append_json_string(std::string& out, std::string_view text) {
    out += '"';
    for (const char ch : text) {
        switch (ch) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                // Remaining control characters must be \u-escaped (RFC 8259).
                if (static_cast<unsigned char>(ch) < 0x20) {
                    out += std::format("\\u{:04x}", static_cast<unsigned>(ch));
                } else {
                    out += ch;
                }
        }
    }
    out += '"';
}

void append_json_number(std::string& out, double value) {
    if (!std::isfinite(value)) {
        out += "null";  // JSON has no NaN/Infinity
        return;
    }
    char buffer[32];
    const auto [end, error] = std::to_chars(buffer, buffer + sizeof buffer, value);
    if (error != std::errc{}) {
        throw std::runtime_error("to_json: failed to format number");
    }
    out.append(buffer, end);
}

void append_json_number(std::string& out, std::size_t value) { out += std::to_string(value); }

// Writes `"key": ` with the given indentation.
void append_key(std::string& out, int indent, std::string_view key) {
    out.append(static_cast<std::size_t>(indent), ' ');
    append_json_string(out, key);
    out += ": ";
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
    for (const double sample : result.samples_ms) {
        if (!(sample >= 0.0)) {  // also rejects NaN
            throw std::invalid_argument("make_profile_record: samples must be non-negative");
        }
    }
    ProfileRecord record;
    record.statistics = compute_statistics(result.samples_ms);  // validates non-empty/finite
    record.metadata = std::move(metadata);
    record.config = config;
    record.iterations_per_sample = result.iterations_per_sample;
    record.samples_ms = result.samples_ms;
    return record;
}

std::string compiler_description() {
#if defined(__clang__) && defined(__apple_build_version__)
    return std::format("Apple clang {}.{}.{}", __clang_major__, __clang_minor__,
                       __clang_patchlevel__);
#elif defined(__clang__)
    return std::format("clang {}.{}.{}", __clang_major__, __clang_minor__, __clang_patchlevel__);
#elif defined(__GNUC__)
    return std::format("gcc {}.{}.{}", __GNUC__, __GNUC_MINOR__, __GNUC_PATCHLEVEL__);
#elif defined(_MSC_VER)
    return std::format("MSVC {}", _MSC_FULL_VER);
#else
    return "unknown";
#endif
}

std::string utc_timestamp_now() {
    const auto now = std::chrono::floor<std::chrono::seconds>(std::chrono::system_clock::now());
    return std::format("{:%FT%TZ}", now);
}

std::string to_json(std::span<const ProfileRecord> records, const std::string& generated_at_utc) {
    std::string out = "{\n";
    append_key(out, 2, "schema");
    append_json_string(out, kProfileSchema);
    out += ",\n";
    append_key(out, 2, "generated_at_utc");
    append_json_string(out, generated_at_utc);
    out += ",\n";
    append_key(out, 2, "time_unit");
    append_json_string(out, "ms");
    out += ",\n";
    append_key(out, 2, "results");
    out += "[";

    for (std::size_t i = 0; i < records.size(); ++i) {
        const ProfileRecord& r = records[i];
        const SampleStatistics& s = r.statistics;
        out += i == 0 ? "\n    {\n" : ",\n    {\n";

        const auto field_string = [&](std::string_view key, std::string_view value) {
            append_key(out, 6, key);
            append_json_string(out, value);
            out += ",\n";
        };
        const auto field_count = [&](int indent, std::string_view key, std::size_t value) {
            append_key(out, indent, key);
            append_json_number(out, value);
            out += ",\n";
        };
        const auto field_double = [&](int indent, std::string_view key, double value) {
            append_key(out, indent, key);
            append_json_number(out, value);
            out += ",\n";
        };

        field_string("workload", r.metadata.workload);
        field_count(6, "input_size", r.metadata.input_size);
        field_count(6, "thread_count", r.metadata.thread_count);
        field_string("compiler", r.metadata.compiler);
        field_string("build_type", r.metadata.build_type);
        field_string("timestamp_utc", r.metadata.timestamp_utc);

        append_key(out, 6, "config");
        out += "{\n";
        field_count(8, "warmup_runs", r.config.warmup_runs);
        field_count(8, "measured_runs", r.config.measured_runs);
        field_double(8, "min_warmup_ms", r.config.min_warmup_ms);
        append_key(out, 8, "min_sample_ms");
        append_json_number(out, r.config.min_sample_ms);
        out += "\n      },\n";

        field_count(6, "iterations_per_sample", r.iterations_per_sample);

        append_key(out, 6, "statistics");
        out += "{\n";
        field_count(8, "count", s.count);
        field_double(8, "min", s.min);
        field_double(8, "max", s.max);
        field_double(8, "mean", s.mean);
        field_double(8, "median", s.median);
        append_key(out, 8, "stddev");
        if (s.stddev) {
            append_json_number(out, *s.stddev);
        } else {
            out += "null";
        }
        out += ",\n";
        append_key(out, 8, "p95");
        append_json_number(out, s.p95);
        out += "\n      },\n";

        append_key(out, 6, "samples");
        out += "[";
        for (std::size_t j = 0; j < r.samples_ms.size(); ++j) {
            if (j > 0) {
                out += ", ";
            }
            append_json_number(out, r.samples_ms[j]);
        }
        out += "]\n    }";
    }
    out += records.empty() ? "]\n}\n" : "\n  ]\n}\n";
    return out;
}

void write_json_file(const std::filesystem::path& path, std::span<const ProfileRecord> records,
                     const std::string& generated_at_utc) {
    const std::string json = to_json(records, generated_at_utc);
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
        std::filesystem::remove(temporary, error);
        throw std::runtime_error("write_json_file: cannot replace " + path.string());
    }
}

}  // namespace aether
