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

# Same strict warning set for every target in the project.
function(aether_enable_warnings target)
    target_compile_options(${target} PRIVATE
        $<$<CXX_COMPILER_ID:MSVC>:/W4>
        $<$<NOT:$<CXX_COMPILER_ID:MSVC>>:-Wall -Wextra -Wpedantic -Wconversion -Wshadow>
    )
endfunction()

# Core library: kernels and benchmark utilities shared by tools and tests.
add_library(aether_core STATIC
    src/vector_add.cpp
    src/benchmark.cpp
    src/matrix.cpp
    src/traversal.cpp
    src/parallel.cpp
    src/profiling.cpp
)
target_include_directories(aether_core PUBLIC include)
# std::thread needs the platform thread library (-pthread on Linux; a no-op on macOS).
find_package(Threads REQUIRED)
target_link_libraries(aether_core PUBLIC Threads::Threads)
aether_enable_warnings(aether_core)

add_executable(aether_engine src/main.cpp)
aether_enable_warnings(aether_engine)

add_executable(aether_vector_add_bench bench/vector_add_bench.cpp)
target_link_libraries(aether_vector_add_bench PRIVATE aether_core)
target_compile_definitions(aether_vector_add_bench PRIVATE AETHER_BUILD_TYPE="$<CONFIG>")
aether_enable_warnings(aether_vector_add_bench)

add_executable(aether_matrix_traversal_bench bench/matrix_traversal_bench.cpp)
target_link_libraries(aether_matrix_traversal_bench PRIVATE aether_core)
target_compile_definitions(aether_matrix_traversal_bench PRIVATE AETHER_BUILD_TYPE="$<CONFIG>")
aether_enable_warnings(aether_matrix_traversal_bench)

add_executable(aether_parallel_vector_add_bench bench/parallel_vector_add_bench.cpp)
target_link_libraries(aether_parallel_vector_add_bench PRIVATE aether_core)
target_compile_definitions(aether_parallel_vector_add_bench PRIVATE AETHER_BUILD_TYPE="$<CONFIG>")
aether_enable_warnings(aether_parallel_vector_add_bench)

add_executable(aether_cpu_profile bench/cpu_profile.cpp)
target_link_libraries(aether_cpu_profile PRIVATE aether_core)
target_compile_definitions(aether_cpu_profile PRIVATE AETHER_BUILD_TYPE="$<CONFIG>")
aether_enable_warnings(aether_cpu_profile)

enable_testing()
add_executable(aether_core_tests tests/core_tests.cpp)
target_link_libraries(aether_core_tests PRIVATE aether_core)
aether_enable_warnings(aether_core_tests)
add_test(NAME aether_core_tests COMMAND aether_core_tests)

add_executable(aether_profiling_tests tests/profiling_tests.cpp)
target_link_libraries(aether_profiling_tests PRIVATE aether_core)
aether_enable_warnings(aether_profiling_tests)
add_test(NAME aether_profiling_tests COMMAND aether_profiling_tests)
