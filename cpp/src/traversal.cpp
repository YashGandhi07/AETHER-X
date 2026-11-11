#include "aether/traversal.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <string>

#include "aether/span_utils.hpp"

namespace aether {
namespace {

void validate_output(const char* caller, const Matrix& m, std::span<const float> sums) {
    if (sums.size() != m.rows()) {
        throw std::invalid_argument(std::string(caller) + ": expected " +
                                    std::to_string(m.rows()) + " sums, got " +
                                    std::to_string(sums.size()));
    }
    // Writing a sum into the matrix would corrupt elements not yet read.
    if (ranges_overlap(sums, m.values())) {
        throw std::invalid_argument(std::string(caller) + ": output overlaps matrix storage");
    }
}

// Error bound gamma(k) = k*u / (1 - k*u) for k sequential roundings.
double gamma(std::size_t k, double unit_roundoff) {
    const double ku = static_cast<double>(k) * unit_roundoff;
    return ku < 1.0 ? ku / (1.0 - ku) : std::numeric_limits<double>::infinity();
}

}  // namespace

void row_sums_row_major(const Matrix& m, std::span<float> sums) {
    validate_output("row_sums_row_major", m, sums);
    const std::size_t rows = m.rows();
    const std::size_t cols = m.cols();

    for (std::size_t r = 0; r < rows; ++r) {
        // One running total per row; the inner loop reads consecutive floats,
        // so every byte of each 128-byte cache line fetched is used.
        float total = 0.0f;
        for (std::size_t c = 0; c < cols; ++c) {
            total += m(r, c);
        }
        sums[r] = total;
    }
}

void row_sums_column_major(const Matrix& m, std::span<float> sums) {
    validate_output("row_sums_column_major", m, sums);
    const std::size_t rows = m.rows();
    const std::size_t cols = m.cols();

    std::fill(sums.begin(), sums.end(), 0.0f);
    for (std::size_t c = 0; c < cols; ++c) {
        // Consecutive reads are a full row apart: each touches a different
        // cache line (and, for rows >= 16 KiB, a different memory page).
        for (std::size_t r = 0; r < rows; ++r) {
            sums[r] += m(r, c);
        }
    }
}

std::optional<std::size_t> find_first_bad_row_sum(const Matrix& m, std::span<const float> sums) {
    if (sums.size() != m.rows()) {
        throw std::invalid_argument("find_first_bad_row_sum: expected " +
                                    std::to_string(m.rows()) + " sums, got " +
                                    std::to_string(sums.size()));
    }
    constexpr double kFloatUnitRoundoff = 0x1p-24;
    constexpr double kDoubleUnitRoundoff = 0x1p-53;
    const std::size_t cols = m.cols();
    // The double reference has its own (far smaller) a-priori error, which is
    // added so the check never rejects a correctly rounded result.
    const double reference_error_factor = gamma(cols, kDoubleUnitRoundoff);

    for (std::size_t r = 0; r < m.rows(); ++r) {
        double reference = 0.0;
        double magnitude = 0.0;
        double partial_sum_magnitudes = 0.0;
        float partial = 0.0f;
        for (std::size_t c = 0; c < cols; ++c) {
            const float x = m(r, c);
            reference += static_cast<double>(x);
            magnitude += std::abs(static_cast<double>(x));
            partial += x;
            partial_sum_magnitudes += std::abs(static_cast<double>(partial));
        }
        // The (1 + 2u) factor covers rounding in accumulating the bound itself.
        const double allowed = kFloatUnitRoundoff * partial_sum_magnitudes *
                                   (1.0 + 2.0 * kFloatUnitRoundoff) +
                               reference_error_factor * magnitude;
        if (!(std::abs(static_cast<double>(sums[r]) - reference) <= allowed)) {
            return r;
        }
    }
    return std::nullopt;
}

}  // namespace aether
