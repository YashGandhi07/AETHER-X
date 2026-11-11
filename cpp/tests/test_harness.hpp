#pragma once

// Minimal self-contained test harness shared by the AETHER X test executables.
// Each executable is registered with CTest and exits non-zero on any failure.

#include <cstddef>
#include <iostream>
#include <source_location>
#include <span>
#include <string_view>

namespace aether::test {

inline int g_failures = 0;

inline void check(bool condition, std::string_view description,
                  std::source_location where = std::source_location::current()) {
    if (!condition) {
        ++g_failures;
        std::cerr << "  " << where.file_name() << ':' << where.line() << ": FAILED: " << description
                  << '\n';
    }
}

template <typename Exception, typename Fn>
void check_throws(Fn&& fn, std::string_view description,
                  std::source_location where = std::source_location::current()) {
    bool threw_expected = false;
    try {
        fn();
    } catch (const Exception&) {
        threw_expected = true;
    } catch (...) {
    }
    check(threw_expected, description, where);
}

struct TestCase {
    std::string_view name;
    void (*fn)();
};

// Runs every test, prints one line per test and a summary; returns the exit code.
inline int run_tests(std::span<const TestCase> tests) {
    std::size_t failed_tests = 0;
    for (const TestCase& test : tests) {
        const int failures_before = g_failures;
        test.fn();
        const bool passed = g_failures == failures_before;
        failed_tests += passed ? 0 : 1;
        std::cout << (passed ? "[PASS] " : "[FAIL] ") << test.name << '\n';
    }
    std::cout << (tests.size() - failed_tests) << '/' << tests.size() << " tests passed\n";
    return failed_tests == 0 ? 0 : 1;
}

}  // namespace aether::test
