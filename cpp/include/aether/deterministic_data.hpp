#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

namespace aether {

// SplitMix64 (Steele, Lea & Flood, 2014). Used instead of <random>
// distributions because their output is implementation-defined: benchmark
// inputs must be bit-identical across Apple clang, GCC and MSVC.
class SplitMix64 {
public:
    explicit constexpr SplitMix64(std::uint64_t seed) noexcept : state_(seed) {}

    constexpr std::uint64_t next() noexcept {
        std::uint64_t z = (state_ += 0x9E3779B97F4A7C15ULL);
        z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
        z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
        return z ^ (z >> 31);
    }

private:
    std::uint64_t state_;
};

// Returns `count` floats in [-1, 1). The top 24 random bits form k in
// [0, 2^24), and k * 2^-23 - 1 is exactly representable as a float, so no
// rounding occurs and the values are reproducible on every platform.
inline std::vector<float> make_deterministic_floats(std::size_t count, std::uint64_t seed) {
    constexpr float kScale = 1.0f / 8388608.0f;  // 2^-23
    SplitMix64 rng(seed);
    std::vector<float> values(count);
    for (float& value : values) {
        value = static_cast<float>(rng.next() >> 40) * kScale - 1.0f;
    }
    return values;
}

}  // namespace aether
