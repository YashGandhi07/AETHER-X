#pragma once

#include <cstddef>
#include <optional>
#include <span>

#include "aether/matrix.hpp"

namespace aether {

// Row sums, sums[r] = m(r, 0) + m(r, 1) + ... + m(r, cols - 1), computed with
// two loop orders. Both add each row's elements in ascending column order, so
// they execute the same float additions in the same order and produce
// bit-identical results. Only the memory access pattern differs:
//
//   row-major:    walks memory contiguously (stride 4 bytes);
//   column-major: jumps cols * 4 bytes between consecutive reads.
//
// Both throw std::invalid_argument if sums.size() != m.rows() or if `sums`
// overlaps the matrix storage.
void row_sums_row_major(const Matrix& m, std::span<float> sums);
void row_sums_column_major(const Matrix& m, std::span<float> sums);

// Checks each sums[r] against a double-precision reference. For sequential
// float summation, step k rounds with error at most u * |s_k|, where s_k is
// the partial sum after that step and u = 2^-24 (Higham, "Accuracy and
// Stability of Numerical Algorithms", 2nd ed., sec. 4.2), so
//
//   |computed - exact| <= u * sum_k |s_k|
//
// This a-posteriori bound is rigorous for the ascending-column order the
// kernels use, and about sqrt(cols) times tighter than the a-priori
// gamma(n - 1) * sum|x| bound, which is too loose to catch a missing element
// on long rows. Returns the first row outside the bound (NaN always fails), or
// std::nullopt. Throws std::invalid_argument on size mismatch.
std::optional<std::size_t> find_first_bad_row_sum(const Matrix& m, std::span<const float> sums);

}  // namespace aether
