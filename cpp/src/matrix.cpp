#include "aether/matrix.hpp"

#include <limits>
#include <stdexcept>
#include <string>
#include <utility>

namespace aether {
namespace {

std::size_t checked_element_count(std::size_t rows, std::size_t cols) {
    if (rows != 0 && cols > std::numeric_limits<std::size_t>::max() / rows) {
        throw std::length_error("Matrix: rows * cols overflows std::size_t");
    }
    return rows * cols;
}

}  // namespace

Matrix::Matrix(std::size_t rows, std::size_t cols)
    : rows_(rows), cols_(cols), values_(checked_element_count(rows, cols)) {}

Matrix::Matrix(std::size_t rows, std::size_t cols, std::vector<float> values)
    : rows_(rows), cols_(cols), values_(std::move(values)) {
    const std::size_t expected = checked_element_count(rows, cols);
    if (values_.size() != expected) {
        throw std::invalid_argument("Matrix: expected " + std::to_string(expected) +
                                    " values for " + std::to_string(rows) + "x" +
                                    std::to_string(cols) + ", got " +
                                    std::to_string(values_.size()));
    }
}

float Matrix::at(std::size_t row, std::size_t col) const {
    if (row >= rows_ || col >= cols_) {
        throw std::out_of_range("Matrix::at: (" + std::to_string(row) + ", " + std::to_string(col) +
                                ") outside " + std::to_string(rows_) + "x" +
                                std::to_string(cols_));
    }
    return (*this)(row, col);
}

}  // namespace aether
