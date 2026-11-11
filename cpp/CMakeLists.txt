cmake_minimum_required(VERSION 3.20)

project(AetherXEngine
    VERSION 0.1.0
    DESCRIPTION "AETHER X C++ engine"
    LANGUAGES CXX
)

# Require C++20 and disable compiler-specific extensions (e.g. gnu++20).
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Export compile_commands.json for editor tooling (clangd / Cursor).
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# Default to a Release build when no build type is given.
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

add_executable(aether_engine src/main.cpp)

target_compile_options(aether_engine PRIVATE
    $<$<CXX_COMPILER_ID:MSVC>:/W4>
    $<$<NOT:$<CXX_COMPILER_ID:MSVC>>:-Wall -Wextra -Wpedantic>
)
