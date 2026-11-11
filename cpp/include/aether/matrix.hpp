#pragma once

#include <cstddef>
#include <span>
#include <vector>

namespace aether {

// Dense row-major float matrix: element (r, c) lives at index r * cols + c of
// a single contiguous std::vector.
class Matrix {
public:
    // Zero-initialized. Throws std::length_error if rows * cols overflows.
    Matrix(std::size_t rows, std::size_t cols);

    // Takes ownership of `values` in row-major order. Throws
    // std::invalid_argument if values.size() != rows * cols.
    Matrix(std::size_t rows, std::size_t cols, std::vector<float> values);

    std::size_t rows() const noexcept { return rows_; }
    std::size_t cols() const noexcept { return cols_; }

    // Unchecked access for hot loops whose bounds were validated up front.
    float operator()(std::size_t row, std::size_t col) const noexcept {
        return values_[row * cols_ + col];
    }
    float& operator()(std::size_t row, std::size_t col) noexcept {
        return values_[row * cols_ + col];
    }

    // Bounds-checked access. Throws std::out_of_range.
    float at(std::size_t row, std::size_t col) const;

    std::span<const float> values() const noexcept { return values_; }
    std::span<float> values() noexcept { return values_; }

private:
    std::size_t rows_;
    std::size_t cols_;
    std::vector<float> values_;
};

}  // namespace aether
