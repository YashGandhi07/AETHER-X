#include "aether/vector_add.hpp"

#include <cmath>
#include <stdexcept>
#include <string>

#include "aether/span_utils.hpp"

namespace aether {
namespace {

void require_equal_sizes(const char* caller, std::size_t a, std::size_t b, std::size_t out) {
    if (a != b || a != out) {
        throw std::invalid_argument(std::string(caller) + ": size mismatch (a=" + std::to_string(a) +
                                    ", b=" + std::to_string(b) + ", out=" + std::to_string(out) +
                                    ")");
    }
}

// An offset overlap would let a write to out[i] clobber an input element that
// has not been read yet. Identical ranges are safe for an element-wise op.
void require_no_partial_overlap(std::span<const float> input, std::span<const float> out,
                                const char* input_name) {
    if (input.data() != out.data() && ranges_overlap(input, out)) {
        throw std::invalid_argument(std::string("vector_add: output partially overlaps input '") +
                                    input_name + "'");
    }
}

}  // namespace

void vector_add(std::span<const float> a, std::span<const float> b, std::span<float> out) {
    require_equal_sizes("vector_add", a.size(), b.size(), out.size());
    require_no_partial_overlap(a, out, "a");
    require_no_partial_overlap(b, out, "b");

    // Plain indexed loop: with -O3 the compiler vectorizes this (NEON on Apple
    // Silicon), adding a runtime alias check since exact aliasing is permitted.
    for (std::size_t i = 0; i < out.size(); ++i) {
        out[i] = a[i] + b[i];
    }
}

std::optional<std::size_t> find_first_mismatch(std::span<const float> a,
                                                std::span<const float> b,
                                                std::span<const float> out,
                                                Tolerance tolerance) {
    require_equal_sizes("find_first_mismatch", a.size(), b.size(), out.size());
    // Negated comparisons so NaN tolerances are rejected too.
    if (!(tolerance.absolute >= 0.0) || !(tolerance.relative >= 0.0)) {
        throw std::invalid_argument("find_first_mismatch: tolerance must be non-negative");
    }

    for (std::size_t i = 0; i < out.size(); ++i) {
        // The double sum of two floats is exact, so the only expected error in
        // `out` is the final rounding to float (<= 0.5 ulp, ~6e-8 relative).
        const double expected = static_cast<double>(a[i]) + static_cast<double>(b[i]);
        const double allowed = tolerance.absolute + tolerance.relative * std::abs(expected);
        if (!(std::abs(static_cast<double>(out[i]) - expected) <= allowed)) {
            return i;
        }
    }
    return std::nullopt;
}

}  // namespace aether
