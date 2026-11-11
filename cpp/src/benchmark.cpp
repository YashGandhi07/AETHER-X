#include "aether/benchmark.hpp"

#include <algorithm>
#include <chrono>
#include <cstddef>
#include <numeric>

namespace aether {

double median(std::vector<double> values) {
    if (values.empty()) {
        throw std::invalid_argument("median: no values");
    }
    // nth_element is O(n) on average; a full sort is unnecessary.
    const auto middle = values.begin() + static_cast<std::ptrdiff_t>(values.size() / 2);
    std::nth_element(values.begin(), middle, values.end());
    if (values.size() % 2 == 1) {
        return *middle;
    }
    // Everything before `middle` is now <= *middle, so the lower middle value
    // is the largest element of that partition.
    const double lower = *std::max_element(values.begin(), middle);
    return std::midpoint(lower, *middle);
}

BenchmarkResult summarize(std::vector<double> samples_ms) {
    if (samples_ms.empty()) {
        throw std::invalid_argument("summarize: no samples");
    }
    const auto [min_it, max_it] = std::minmax_element(samples_ms.begin(), samples_ms.end());
    BenchmarkResult result;
    result.min_ms = *min_it;
    result.max_ms = *max_it;
    result.median_ms = median(samples_ms);
    result.samples_ms = std::move(samples_ms);
    return result;
}

double measure_clock_resolution_ns() {
    using Clock = std::chrono::steady_clock;
    // Clock::period is only the unit of the representation (1 ns on libc++);
    // the real tick can be coarser, so it has to be observed.
    auto smallest = Clock::duration::max();
    for (int trial = 0; trial < 1000; ++trial) {
        const auto first = Clock::now();
        auto next = Clock::now();
        while (next == first) {
            next = Clock::now();
        }
        smallest = std::min(smallest, next - first);
    }
    return std::chrono::duration<double, std::nano>(smallest).count();
}

}  // namespace aether
