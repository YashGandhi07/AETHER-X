#pragma once

#include <functional>
#include <span>

namespace aether {

// True if the two ranges share at least one element. std::less gives a total
// order over pointers; the built-in < is unspecified for unrelated arrays.
inline bool ranges_overlap(std::span<const float> x, std::span<const float> y) noexcept {
    if (x.empty() || y.empty()) {
        return false;
    }
    const std::less<const float*> less;
    return less(x.data(), y.data() + y.size()) && less(y.data(), x.data() + x.size());
}

}  // namespace aether
