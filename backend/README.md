# AETHER X backend

Two Python packages on top of the C++ engine in `cpp/`:

| Package | Purpose | Dependencies |
|---|---|---|
| `aether_bench` | Runs the compiled C++ tools as subprocesses and validates their JSON | standard library only |
| `aether_api` | FastAPI HTTP service (`/api/v1/...`) built on `aether_bench` | `requirements.txt` |

## Setup (from the repository root)

```bash
# 1. Build the C++ engine (Release by default)
cmake -S cpp -B cpp/build
cmake --build cpp/build

# 2. Create the virtual environment from the pinned dependency files
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements-dev.txt   # or requirements.txt for runtime only
```

`requirements.txt` pins every runtime package (direct and transitive) and
`requirements-dev.txt` adds the test tools, so installs are reproducible.

## Run the API

```bash
backend/.venv/bin/uvicorn aether_api.main:app --app-dir backend --host 127.0.0.1 --port 8000

curl http://127.0.0.1:8000/api/v1/health
curl http://127.0.0.1:8000/api/v1/system
```

Interactive API docs: http://127.0.0.1:8000/docs (OpenAPI schema at `/openapi.json`).

| Endpoint | Returns |
|---|---|
| `GET /api/v1/health` | `status` (`ok`, or `degraded` if the C++ engine is not built), `service`, `version`, `engine_available`. Never runs the engine. |
| `GET /api/v1/system` | Host hardware and engine build metadata from the C++ collector (`aether_system_info`, no benchmarks). Collected once per process and cached. `null` means the platform could not report a value. |

Errors always use one shape, with the request id also sent in the `X-Request-ID` header:

```json
{"error": {"code": "engine_not_built", "message": "The C++ engine is not built on this server.", "request_id": "..."}}
```

| Code | HTTP | Cause |
|---|---|---|
| `engine_not_built` | 503 | `cpp/build/aether_system_info` is missing |
| `engine_timeout` | 504 | the engine exceeded `AETHER_ENGINE_TIMEOUT_S` |
| `engine_failed` / `engine_bad_output` | 502 | non-zero exit / invalid JSON from the engine |
| `not_found` / `method_not_allowed` / `validation_error` | 404 / 405 / 422 | client errors |
| `internal_error` | 500 | anything unexpected (details are logged server-side only) |

### Configuration (environment variables)

| Variable | Default | Notes |
|---|---|---|
| `AETHER_CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | explicit `scheme://host[:port]` list; `*` is rejected |
| `AETHER_ENGINE_TIMEOUT_S` | `10` | must be in (0, 300] |
| `AETHER_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

Invalid values stop the service at startup. CORS allows only `GET`, without
credentials, because the API has no authentication yet.

Logs are JSON lines on stderr (`aether.api.access` has one entry per request
with `request_id`, `method`, `path` (no query string), `status` and
`duration_ms`).

## Run the tests

```bash
backend/.venv/bin/pytest backend
```

This runs the `aether_bench` tests (fake executables, synthetic fixtures
labelled `TEST_FIXTURE`), the API tests (FastAPI `TestClient` with a synthetic
metadata provider) and two integration tests against the real C++ binaries.
The integration tests are skipped when the engine is not built; set
`AETHER_REQUIRE_INTEGRATION=1` to make that a failure instead.

## Command-line profiler (no API)

```bash
PYTHONPATH=backend python3 -m aether_bench            # summary table of a real profile run
PYTHONPATH=backend python3 -m aether_bench --json     # full validated report
```

Exit codes: `0` success, `1` benchmark failed or invalid report, `2` not built
or bad arguments, `3` timeout.

## Design notes (`aether_bench`)

* **No caller-chosen executables:** only names in `KNOWN_EXECUTABLES` run,
  resolved inside `cpp/build`.
* **No shell:** argument lists with `shell=False`; no request data is ever
  passed to a process.
* **Isolated child process:** minimal environment (`PATH`, `LC_ALL`), no stdin,
  explicit timeout.
* **Strict validation:** types, ranges, identifier formats, unique ids, and
  every statistic recomputed from the raw samples. Unknown extra fields are
  ignored for forward compatibility.
