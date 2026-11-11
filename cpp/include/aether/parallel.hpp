#pragma once

#include <cstddef>
#include <span>
#include <vector>

namespace aether {

struct IndexRange {
    std::size_t begin = 0;
    std::size_t end = 0;  // exclusive

    std::size_t size() const noexcept { return end - begin; }
    bool operator==(const IndexRange&) const = default;
};

// Splits [0, count) into `parts` contiguous, in-order ranges whose sizes
// differ by at most one (the first count % parts ranges get the extra
// element). Ranges are empty when parts > count. Throws std::invalid_argument
// if parts == 0.
std::vector<IndexRange> partition_range(std::size_t count, std::size_t parts);

// Threads worth using: `requested`, capped at std::thread::hardware_concurrency()
// (when the platform reports it) and at one thread per `min_elements_per_thread`
// elements, so no thread is started without a meaningful amount of work.
// Always >= 1. Throws std::invalid_argument if requested or
// min_elements_per_thread is 0.
std::size_t plan_thread_count(std::size_t requested, std::size_t element_count,
                              std::size_t min_elements_per_thread);

// From Section 2.3 measurements on an Apple M5: starting and joining one extra
// std::thread costs ~13 us, and sequential addition runs at ~0.093 ns per
// element on cache-resident data. A thread therefore only pays for itself once
// it takes over roughly 13 us / 0.093 ns ~= 140k elements, i.e. ~2^17.
inline constexpr std::size_t kDefaultMinElementsPerThread = std::size_t{1} << 17;

// Multithreaded out[i] = a[i] + b[i]. Same validation and aliasing rules as
// vector_add, checked on the whole arrays before any thread starts. Each
// thread writes one disjoint chunk of `out`, so there is no shared mutable
// state and no synchronization beyond the final join. The calling thread
// processes the first chunk itself. Returns the number of threads used.
std::size_t vector_add_parallel(std::span<const float> a, std::span<const float> b,
                                std::span<float> out, std::size_t requested_threads,
                                std::size_t min_elements_per_thread = kDefaultMinElementsPerThread);

}  // namespace aether
