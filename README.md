# AETHER X

AETHER X is an advanced AI/ML, GPU optimization, and software engineering project. It brings together model development, high-performance GPU and C++ systems work, and a production-style web stack in one repository.

## Goals

- Build and train machine learning models end to end
- Write and tune GPU kernels (CUDA) and measure the speedups carefully
- Engineer performance-critical systems in modern C++
- Serve models and tools through a FastAPI backend and a React/Next.js frontend
- Back every performance claim with reproducible benchmarks and automated tests

## Development environment

- **Primary:** MacBook (application, ML, and C++ development)
- **GPU work:** NVIDIA RTX 4060 Ti PC (CUDA kernels and GPU experiments)

## Repository layout

| Folder        | Purpose                                         |
|---------------|-------------------------------------------------|
| `backend/`    | FastAPI services and REST APIs                  |
| `frontend/`   | React / Next.js / TypeScript web app            |
| `ml/`         | Machine learning models, training, and evaluation |
| `gpu/`        | GPU kernels (CUDA) and GPU optimization work    |
| `cpp/`        | C++ systems and performance engineering         |
| `benchmarks/` | Performance experiments and results             |
| `tests/`      | Automated tests across the project              |
| `docs/`       | Architecture notes and documentation            |
| `infra/`      | Docker, deployment, and environment setup       |

## Building the C++ engine

Requirements: a C++20 compiler (tested with Apple clang 21) and CMake 3.20 or newer.

```bash
cmake -S cpp -B cpp/build      # configure (Release by default)
cmake --build cpp/build        # compile
./cpp/build/aether_engine      # prints: AETHER X - C++ Engine Ready
```

Build output in `cpp/build/` is ignored by Git.

## Status

Initial setup is complete:

- [x] Repository folder structure
- [x] Git repository and `.gitignore`
- [x] C++20 + CMake build with a minimal executable

Everything else listed under Goals has not been implemented yet. The ML, GPU, backend, and frontend folders are currently empty placeholders.
