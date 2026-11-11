// Prints this host's hardware and build metadata (schema aether.system_info.v1)
// to stdout. Runs no benchmarks, so it is cheap enough to call from a service.
// Usage: aether_system_info

#include <exception>
#include <iostream>

#include "aether/profiling.hpp"
#include "aether/system_info.hpp"

int main(int argc, char** argv) {
    if (argc != 1) {
        std::cerr << "usage: " << argv[0] << " (takes no arguments)\n";
        return 2;
    }
    try {
        std::cout << aether::environment_to_json(aether::detect_system_info(), aether::build_info(),
                                                 aether::utc_timestamp_now());
        std::cout.flush();
        return std::cout ? 0 : 1;
    } catch (const std::exception& error) {
        std::cerr << "error: " << error.what() << '\n';
        return 1;
    }
}
