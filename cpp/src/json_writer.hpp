#pragma once

// Internal streaming JSON writer used by the profiling export. Produces
// 2-space-indented output, RFC 8259 string escaping, and the shortest double
// representation that round-trips exactly. Structural misuse (a value without
// a key inside an object, unbalanced nesting) throws std::logic_error.

#include <charconv>
#include <cmath>
#include <concepts>
#include <cstddef>
#include <format>
#include <optional>
#include <span>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <utility>
#include <vector>

namespace aether::detail {

class JsonWriter {
public:
    void begin_object() { open('{', true); }
    void end_object() { close('}', true); }
    void begin_array() { open('[', false); }
    void end_array() { close(']', false); }

    void key(std::string_view name) {
        if (stack_.empty() || !stack_.back().is_object || after_key_) {
            throw std::logic_error("JsonWriter: key outside an object or after another key");
        }
        start_element();
        append_string(name);
        out_ += ": ";
        after_key_ = true;
    }

    void value(std::string_view text) {
        begin_value();
        append_string(text);
    }
    // Without this overload a string literal would bind to value(bool).
    void value(const char* text) { value(std::string_view(text)); }
    void value(bool flag) {
        begin_value();
        out_ += flag ? "true" : "false";
    }
    void value(double number) {
        begin_value();
        append_number(number);
    }
    template <std::unsigned_integral T>
        requires(!std::same_as<T, bool>)
    void value(T number) {
        begin_value();
        out_ += std::to_string(number);
    }
    template <typename T>
    void value(const std::optional<T>& maybe) {
        if (maybe) {
            value(*maybe);
        } else {
            null();
        }
    }
    void null() {
        begin_value();
        out_ += "null";
    }

    // Writes [a, b, c] on one line: sample arrays stay readable in a diff.
    void number_array(std::span<const double> numbers) {
        begin_value();
        out_ += '[';
        for (std::size_t i = 0; i < numbers.size(); ++i) {
            if (i > 0) {
                out_ += ", ";
            }
            append_number(numbers[i]);
        }
        out_ += ']';
    }

    template <typename T>
    void field(std::string_view name, const T& v) {
        key(name);
        value(v);
    }

    std::string take() {
        if (!stack_.empty() || after_key_) {
            throw std::logic_error("JsonWriter: document is incomplete");
        }
        out_ += '\n';
        return std::move(out_);
    }

private:
    struct Level {
        bool is_object;
        bool empty = true;
    };

    // Comma, newline and indentation before an object member or array element.
    void start_element() {
        if (stack_.empty()) {
            return;
        }
        Level& top = stack_.back();
        if (!top.empty) {
            out_ += ',';
        }
        top.empty = false;
        out_ += '\n';
        out_.append(2 * stack_.size(), ' ');
    }

    void begin_value() {
        if (after_key_) {
            after_key_ = false;
            return;
        }
        if (!stack_.empty() && stack_.back().is_object) {
            throw std::logic_error("JsonWriter: object member written without a key");
        }
        if (stack_.empty() && !out_.empty()) {
            throw std::logic_error("JsonWriter: more than one top-level value");
        }
        start_element();
    }

    void open(char bracket, bool is_object) {
        begin_value();
        out_ += bracket;
        stack_.push_back({is_object});
    }

    void close(char bracket, bool is_object) {
        if (stack_.empty() || stack_.back().is_object != is_object || after_key_) {
            throw std::logic_error("JsonWriter: unbalanced nesting");
        }
        const bool was_empty = stack_.back().empty;
        stack_.pop_back();
        if (!was_empty) {
            out_ += '\n';
            out_.append(2 * stack_.size(), ' ');
        }
        out_ += bracket;
    }

    void append_string(std::string_view text) {
        out_ += '"';
        for (const char ch : text) {
            switch (ch) {
                case '"': out_ += "\\\""; break;
                case '\\': out_ += "\\\\"; break;
                case '\n': out_ += "\\n"; break;
                case '\r': out_ += "\\r"; break;
                case '\t': out_ += "\\t"; break;
                default:
                    // Remaining control characters must be \u-escaped (RFC 8259).
                    if (static_cast<unsigned char>(ch) < 0x20) {
                        out_ += std::format("\\u{:04x}", static_cast<unsigned>(ch));
                    } else {
                        out_ += ch;
                    }
            }
        }
        out_ += '"';
    }

    void append_number(double number) {
        if (!std::isfinite(number)) {
            out_ += "null";  // JSON has no NaN or Infinity
            return;
        }
        char buffer[32];
        const auto [end, error] = std::to_chars(buffer, buffer + sizeof buffer, number);
        if (error != std::errc{}) {
            throw std::runtime_error("JsonWriter: failed to format number");
        }
        out_.append(buffer, end);
    }

    std::string out_;
    std::vector<Level> stack_;
    bool after_key_ = false;
};

}  // namespace aether::detail
