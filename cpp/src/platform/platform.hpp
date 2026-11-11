#pragma once

#include "aether/system_info.hpp"

namespace aether::platform {

// Fills the OS- and hardware-specific fields of `info`. Exactly one
// implementation is compiled in, chosen by CMake:
//   system_info_macos.cpp    - sysctl / uname
//   system_info_fallback.cpp - portable C++ only (other platforms, for now)
// Implementations must leave a field empty rather than guess its value.
void fill_system_info(SystemInfo& info);

}  // namespace aether::platform
