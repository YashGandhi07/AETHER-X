# AETHER X

AETHER X is an advanced AI/ML, GPU optimization, and software engineering project. It brings together model development, high-performance GPU and C++ systems work, and a production-style web stack in one repository.

## Goals

- Build and train machine learning models end to end
- Write and tune GPU kernels (CUDA) and measure the speedups carefully
- Engineer performance-critical systems in modern C++
- Serve models and tools through a FastAPI backend and a React/Next.js frontend
- Back every performance claim with reproducible benchmarks and automated tests

## Development environment

- **Primary:** MacBook
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
ctest --test-dir cpp/build     # run the C++ test suites
```

This builds the command-line tools used by the backend (`aether_cpu_profile`,
`aether_system_info`, `aether_run_benchmark`), the standalone benchmarks and
the tests; `aether_engine` is only a build smoke test. Build output in
`cpp/build/` is ignored by Git. See [`backend/README.md`](backend/README.md)
for the Python packages, the API, PostgreSQL setup and the Python tests.

## Status

Implemented:

- [x] **C++20 CPU benchmark engine** (`cpp/`): `vector_add`, `std::thread`
      parallel `vector_add`, and row- vs column-major matrix traversal. Every
      output is checked against a reference; reports contain every sample,
      summary statistics, and hardware and build metadata.
- [x] **Python interface** (`backend/aether_bench`): runs the engine without a
      shell and strictly validates its JSON reports.
- [x] **FastAPI backend** (`backend/aether_api`, localhost only): health, system
      metadata and benchmark execution endpoints.
- [x] **PostgreSQL storage**: experiment schema with Alembic migrations; every
      benchmark run is saved, with history and lookup endpoints.

Not started yet: `gpu/`, `ml/`, `frontend/`, `infra/` and `docs/` are placeholders.
