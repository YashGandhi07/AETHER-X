#include "aether/experiment_id.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <random>

namespace aether {

std::string make_experiment_id() {
    // std::random_device is non-deterministic on the supported toolchains
    // (arc4random on macOS, /dev/urandom on Linux, RtlGenRandom on Windows).
    // A fresh device per call keeps this thread-safe without a shared engine.
    std::random_device device;
    std::array<std::uint8_t, 16> bytes{};
    for (std::size_t i = 0; i < bytes.size(); i += 4) {
        const std::uint32_t word = device();
        for (std::size_t j = 0; j < 4; ++j) {
            bytes[i + j] = static_cast<std::uint8_t>(word >> (8 * j));
        }
    }
    bytes[6] = static_cast<std::uint8_t>((bytes[6] & 0x0F) | 0x40);  // version 4
    bytes[8] = static_cast<std::uint8_t>((bytes[8] & 0x3F) | 0x80);  // RFC variant (10xx)

    constexpr char kHex[] = "0123456789abcdef";
    std::string id;
    id.reserve(36);
    for (std::size_t i = 0; i < bytes.size(); ++i) {
        if (i == 4 || i == 6 || i == 8 || i == 10) {
            id += '-';
        }
        id += kHex[bytes[i] >> 4];
        id += kHex[bytes[i] & 0x0F];
    }
    return id;
}

bool is_valid_experiment_id(const std::string& id) noexcept {
    if (id.size() != 36) {
        return false;
    }
    for (std::size_t i = 0; i < id.size(); ++i) {
        const char ch = id[i];
        if (i == 8 || i == 13 || i == 18 || i == 23) {
            if (ch != '-') {
                return false;
            }
        } else if (!((ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f'))) {
            return false;
        }
    }
    const char variant = id[19];
    return id[14] == '4' && (variant == '8' || variant == '9' || variant == 'a' || variant == 'b');
}

}  // namespace aether
