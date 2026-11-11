#include "aether/parallel.hpp"

#include <algorithm>
#include <exception>
#include <stdexcept>
#include <string>
#include <thread>

#include "aether/span_utils.hpp"
#include "aether/vector_add.hpp"

namespace aether {
namespace {

// Joins every started thread on scope exit, including when starting a later
// thread throws std::system_error: destroying a joinable std::thread calls
// std::terminate.
class JoinAll {
public:
    explicit JoinAll(std::vector<std::thread>& threads) noexcept : threads_(threads) {}
    JoinAll(const JoinAll&) = delete;
    JoinAll& operator=(const JoinAll&) = delete;
    ~JoinAll() {
        for (std::thread& thread : threads_) {
            if (thread.joinable()) {
                thread.join();
            }
        }
    }

private:
    std::vector<std::thread>& threads_;
};

// hardware_concurrency() costs ~210 ns per call on macOS (measured in Section
// 2.3), more than a whole 4096-element add, so query it once. Function-local
// static initialization is thread-safe.
unsigned cached_hardware_concurrency() noexcept {
    static const unsigned hardware = std::thread::hardware_concurrency();
    return hardware;
}

}  // namespace

std::vector<IndexRange> partition_range(std::size_t count, std::size_t parts) {
    if (parts == 0) {
        throw std::invalid_argument("partition_range: parts must be at least 1");
    }
    const std::size_t base = count / parts;
    const std::size_t extra = count % parts;

    std::vector<IndexRange> ranges(parts);
    std::size_t begin = 0;
    for (std::size_t i = 0; i < parts; ++i) {
        const std::size_t size = base + (i < extra ? 1 : 0);
        ranges[i] = {begin, begin + size};
        begin += size;
    }
    return ranges;
}

std::size_t plan_thread_count(std::size_t requested, std::size_t element_count,
                              std::size_t min_elements_per_thread) {
    if (requested == 0) {
        throw std::invalid_argument("plan_thread_count: requested threads must be at least 1");
    }
    if (min_elements_per_thread == 0) {
        throw std::invalid_argument("plan_thread_count: min_elements_per_thread must be at least 1");
    }
    std::size_t threads = requested;
    // hardware_concurrency() may return 0 when unknown; then only the work-size cap applies.
    if (const unsigned hardware = cached_hardware_concurrency(); hardware > 0) {
        threads = std::min<std::size_t>(threads, hardware);
    }
    // Ceiling division without overflow.
    const std::size_t useful = element_count / min_elements_per_thread +
                               (element_count % min_elements_per_thread != 0 ? 1 : 0);
    return std::max<std::size_t>(1, std::min(threads, useful));
}

std::size_t vector_add_parallel(std::span<const float> a, std::span<const float> b,
                                std::span<float> out, std::size_t requested_threads,
                                std::size_t min_elements_per_thread) {
    // Validate the whole arrays up front. Per-chunk checks are not enough: an
    // output offset by half the array overlaps a *different* thread's input
    // chunk, which would be a data race that no single chunk can detect.
    if (a.size() != b.size() || a.size() != out.size()) {
        throw std::invalid_argument("vector_add_parallel: size mismatch (a=" +
                                    std::to_string(a.size()) + ", b=" + std::to_string(b.size()) +
                                    ", out=" + std::to_string(out.size()) + ")");
    }
    for (const std::span<const float> input : {a, b}) {
        if (input.data() != out.data() && ranges_overlap(input, out)) {
            throw std::invalid_argument("vector_add_parallel: output partially overlaps an input");
        }
    }

    const std::size_t thread_count =
        plan_thread_count(requested_threads, out.size(), min_elements_per_thread);
    if (thread_count == 1) {
        vector_add(a, b, out);
        return 1;
    }

    // Chunks are contiguous, so each thread streams its own region of memory.
    // Neighbouring chunks can share one cache line at each boundary (false
    // sharing on at most thread_count - 1 lines), which is negligible here.
    const std::vector<IndexRange> chunks = partition_range(out.size(), thread_count);
    const auto add_chunk = [&](const IndexRange& chunk) {
        vector_add(a.subspan(chunk.begin, chunk.size()), b.subspan(chunk.begin, chunk.size()),
                   out.subspan(chunk.begin, chunk.size()));
    };

    // Chunks are pre-validated, so workers should never throw; if one does,
    // capture it instead of letting it escape the thread (std::terminate).
    std::vector<std::exception_ptr> errors(thread_count);
    {
        std::vector<std::thread> workers;
        workers.reserve(thread_count - 1);
        const JoinAll join_all(workers);
        for (std::size_t i = 1; i < thread_count; ++i) {
            workers.emplace_back([&, i] {
                try {
                    add_chunk(chunks[i]);
                } catch (...) {
                    errors[i] = std::current_exception();
                }
            });
        }
        try {
            add_chunk(chunks[0]);
        } catch (...) {
            errors[0] = std::current_exception();
        }
    }  // join_all: every worker has finished, and its writes are visible here.

    for (const std::exception_ptr& error : errors) {
        if (error) {
            std::rethrow_exception(error);
        }
    }
    return thread_count;
}

}  // namespace aether
