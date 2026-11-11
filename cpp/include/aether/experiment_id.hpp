#pragma once

#include <string>

namespace aether {

// Random RFC 9562 UUID version 4, lowercase "xxxxxxxx-xxxx-4xxx-Nxxx-xxxxxxxxxxxx"
// with N in [89ab]. 122 random bits make collisions between experiments
// negligible without any coordination between machines.
std::string make_experiment_id();

// True if `id` has exactly the version-4 UUID layout produced above.
bool is_valid_experiment_id(const std::string& id) noexcept;

}  // namespace aether
