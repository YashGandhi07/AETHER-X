#pragma once

#include <cstddef>
#include <optional>
#include <span>

namespace aether {

// Computes out[i] = a[i] + b[i] for every i.
//
// Throws std::invalid_argument if the three sizes differ, or if `out` partially
// overlaps an input. Exact aliasing (out == a or out == b) is allowed, which
// supports in-place use such as `vector_add(a, b, a)`.
void vector_add(std::span<const float> a, std::span<const float> b, std::span<float> out);

struct Tolerance {
    double absolute = 1e-6;
    double relative = 1e-6;
};

// Checks `out` against a[i] + b[i] computed in double precision. Returns the
// index of the first element outside `absolute + relative * |expected|`, or
// std::nullopt if every element matches. NaN never matches.
//
// Throws std::invalid_argument on size mismatch or a negative/NaN tolerance.
std::optional<std::size_t> find_first_mismatch(std::span<const float> a,
                                                std::span<const float> b,
                                                std::span<const float> out,
                                                Tolerance tolerance = {});

}  // namespace aether
