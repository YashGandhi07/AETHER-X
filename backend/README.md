# AETHER X backend

Two Python packages on top of the C++ engine in `cpp/`:

| Package | Purpose | Dependencies |
|---|---|---|
| `aether_bench` | Runs the compiled C++ tools as subprocesses and validates their JSON | standard library only |
| `aether_api` | FastAPI HTTP service (`/api/v1/...`) built on `aether_bench`, plus the PostgreSQL connection layer | `requirements.txt` |

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

### `POST /api/v1/benchmarks/run` (local development only)

Runs **one predefined workload** in the real C++ engine (`cpp/build/aether_run_benchmark`)
and returns every measured sample, summary statistics, the configuration used, and
the hardware and build metadata of the machine that ran it.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/benchmarks/run \
     -H 'Content-Type: application/json' \
     -d '{"workload": "vector_add_parallel", "size": 1048576, "threads": 4, "warmup_runs": 3, "measured_runs": 15}'
```

| Field | Type | Default | Allowed |
|---|---|---|---|
| `workload` | string | required | `vector_add`, `vector_add_parallel`, `row_sums_row_major`, `row_sums_column_major` |
| `size` | integer | required | vector workloads: elements, 1 – 16,777,216 (2^24); `row_sums_*`: matrix dimension n of n × n, 1 – 4096 |
| `threads` | integer | 1 | `vector_add_parallel`: 1 – logical CPUs of the host; all others: exactly 1 |
| `warmup_runs` | integer | 3 | 0 – 20 (warm-up also always lasts ≥ 100 ms) |
| `measured_runs` | integer | 10 | 1 – 100 |

Response (`200`): `status: "succeeded"`, `run_id`, `generated_at_utc`, `time_unit: "ms"`,
`wall_time_s`, the normalized `request`, `result` (`experiment_id`, `workload`,
`input_size` (elements processed, n·n for matrices), `thread_count` actually used,
`config`, `warmup_runs_executed`, `iterations_per_sample`, `statistics`
{count, min, max, mean, median, stddev, p95}, `samples_ms`), `host` and `engine_build`.

**Behaviour and limitations**

* **Localhost only.** Requests from any non-loopback client get `403 local_only`;
  browser requests with an `Origin` outside `AETHER_CORS_ORIGINS` get
  `403 origin_not_allowed`. There is no authentication yet, so do not expose
  the server beyond `127.0.0.1`. `AETHER_ENABLE_BENCHMARKS=0` disables the
  endpoint (`403 benchmarks_disabled`).
* **Strict input.** Unknown fields, strings/floats/booleans where integers are
  expected, and non-JSON bodies are rejected with `422 validation_error`. The
  engine never receives a request value other than these validated fields:
  no commands, scripts or paths.
* **One run at a time.** A request while another benchmark runs gets
  `429 benchmark_busy` with `Retry-After: 5` (concurrent runs would also distort
  each other's timings). Note that `curl --retry` honours this automatically.
* **Timeout.** Runs are killed after `AETHER_BENCHMARK_TIMEOUT_S` (default 60 s;
  the slowest allowed request measured 5.32 s on an Apple M5) → `504 engine_timeout`.
* **Verified results only.** The C++ engine checks every output against an
  independent reference and writes no report if verification fails
  (`502 engine_failed`). A report that is malformed or does not match the request
  is rejected (`502 engine_bad_output`).
* **Not a browser API yet.** CORS allows only `GET`, so web pages cannot call
  this endpoint until authentication is added.
* Timings depend on the machine and its current load; repeat runs and compare
  medians rather than single values.

### Saved experiments

Every engine run through `POST /api/v1/benchmarks/run` is saved in PostgreSQL
before the response is returned, so a `200` means the measurement is stored.

* **One transaction per run:** the environment (reused if identical), the run,
  the experiment and every timing sample are committed together. The deferred
  sample-count trigger checks the experiment at `COMMIT`; any failure rolls
  back everything and the client gets `503 database_unavailable` or
  `500 database_error` instead of an unsaved result.
* **Exact values:** samples and statistics are stored as reported by the
  engine (`double precision`, bit-for-bit). `wall_time_s` is stored exactly and
  rounded to microseconds in responses.
* **Failures are never measurements:** engine failures are stored as
  `failed` / `timed_out` runs with an error code and no experiment (a missing
  engine records nothing, since nothing ran). The read endpoints return only
  experiments of `succeeded` runs.

| Endpoint (localhost only) | Returns |
|---|---|
| `GET /api/v1/benchmarks?limit=20&offset=0` | saved experiments, newest run first, without samples: `items`, `total`, `limit` (1–100), `offset` (0–100,000), `time_unit` |
| `GET /api/v1/benchmarks/{experiment_id}` | one saved experiment in the same shape as the `POST` response, including every sample; `404 not_found` if unknown or not a UUID |

The read endpoints stay available when `AETHER_ENABLE_BENCHMARKS=0`.

Errors always use one shape, with the request id also sent in the `X-Request-ID` header:

```json
{"error": {"code": "engine_not_built", "message": "The C++ engine is not built on this server.", "request_id": "..."}}
```

| Code | HTTP | Cause |
|---|---|---|
| `engine_not_built` | 503 | an engine executable in `cpp/build` is missing |
| `engine_timeout` | 504 | the engine exceeded its timeout |
| `local_only` / `origin_not_allowed` / `benchmarks_disabled` | 403 | benchmark endpoint access refused |
| `benchmark_busy` | 429 | another benchmark is running (`Retry-After: 5`) |
| `engine_failed` / `engine_bad_output` | 502 | non-zero exit / invalid JSON from the engine |
| `not_found` / `method_not_allowed` / `validation_error` | 404 / 405 / 422 | client errors |
| `database_unavailable` | 503 | PostgreSQL unreachable, pool exhausted or statement timeout |
| `database_error` | 500 | the database rejected the data (e.g. a constraint); nothing was saved |
| `internal_error` | 500 | anything unexpected (details are logged server-side only) |

### Configuration (environment variables)

| Variable | Default | Notes |
|---|---|---|
| `AETHER_CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | explicit `scheme://host[:port]` list; `*` is rejected |
| `AETHER_ENGINE_TIMEOUT_S` | `10` | must be in (0, 300] |
| `AETHER_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `AETHER_ENABLE_BENCHMARKS` | `1` | `1`/`0`/`true`/`false`; the endpoint is localhost-only either way |
| `AETHER_BENCHMARK_TIMEOUT_S` | `60` | must be in (0, 600] |

Invalid values stop the service at startup. CORS allows only `GET`, without
credentials, because the API has no authentication yet.

Logs are JSON lines on stderr (`aether.api.access` has one entry per request
with `request_id`, `method`, `path` (no query string), `status` and
`duration_ms`).

## PostgreSQL connection (`aether_api/database.py`)

The API holds one lazily connected SQLAlchemy engine (psycopg 3 driver). It is
created at startup without contacting the server and disposed on shutdown, so
the API and its unit tests run without PostgreSQL. Tables are managed by Alembic (below).

| Variable | Default | Notes |
|---|---|---|
| `AETHER_DB_HOST` | `localhost` | |
| `AETHER_DB_PORT` | `5432` | |
| `AETHER_DB_NAME` | `aether_x_dev` | plain identifier |
| `AETHER_DB_USER` | `aether_x_app` | plain identifier |
| `AETHER_DB_PASSWORD` | unset | when unset, libpq reads `~/.pgpass` (recommended; outside the repo) |
| `AETHER_DB_CONNECT_TIMEOUT_S` | `5` | integer in [2, 60] |
| `AETHER_DB_STATEMENT_TIMEOUT_MS` | `10000` | enforced by the server per statement |
| `AETHER_DB_POOL_TIMEOUT_S` | `5` | wait for a free pooled connection, (0, 60] |

Store the password in `~/.pgpass` (mode `600`), one line per target:

```
localhost:5432:aether_x_dev:aether_x_app:<password>
```

Never commit credentials; `.env` files are git-ignored. The password is
excluded from `repr`, masked as `***` in URLs, and removed from connection
error messages (`DatabaseUnavailableError`).

```python
from aether_api.database import Database

db = Database.from_environment()
db.check_connection()          # SELECT 1 -> round-trip ms, or DatabaseUnavailableError
with db.session() as session:  # commits on success, rolls back on exception
    ...
db.dispose()
```

## Experiment schema and migrations

Tables (models in `aether_api/db_models.py`, migrations in `migrations/`):

| Table | One row per | Key points |
|---|---|---|
| `environments` | distinct host + engine build | deduplicated by a `NULLS NOT DISTINCT` unique constraint over all metadata |
| `benchmark_runs` | engine invocation | `status` (`running`/`succeeded`/`failed`/`timed_out`), the submitted `request` (JSONB), timings, error code; report fields required once succeeded |
| `experiments` | engine result record | the engine's `experiment_id`, workload, configuration, engine statistics (ms) |
| `timing_samples` | measured sample | `(experiment_id, sample_index)` key, `duration_ms` as `double precision` (bit-exact) |

The database enforces integrity itself: CHECK constraints (including NaN and
Infinity rejection), foreign keys (`ON DELETE CASCADE` from runs to samples,
`RESTRICT` on environments) and a deferred constraint trigger that requires
every experiment to have exactly `measured_runs` samples numbered `0..n-1` at
`COMMIT`. `aether_api/persistence.py` (`BenchmarkStore`) is the only writer (see below).

Apply and inspect migrations (from `backend/`; uses the `AETHER_DB_*` settings,
so `alembic.ini` contains no URL or credentials):

```bash
cd backend
.venv/bin/alembic upgrade head          # apply all migrations
.venv/bin/alembic current               # revision the database is at
.venv/bin/alembic history --verbose     # all revisions
.venv/bin/alembic check                 # fails if models and migrations disagree
.venv/bin/alembic upgrade head --sql    # print the SQL without connecting
AETHER_DB_NAME=aether_x_test .venv/bin/alembic upgrade head   # another database
```

Creating a new migration after changing the models: run
`.venv/bin/alembic revision --autogenerate -m "describe change"`, then review
the generated file (autogenerate cannot see triggers or functions).

Inspect the schema with `psql`:

```bash
PSQL="/opt/homebrew/opt/postgresql@18/bin/psql -h localhost -U aether_x_app -d aether_x_dev"
$PSQL -c '\dt'                   # tables
$PSQL -c '\d+ experiments'       # columns, constraints, indexes, triggers
$PSQL -c "SELECT conrelid::regclass, conname FROM pg_constraint WHERE connamespace = 'public'::regnamespace AND contype IN ('c','f','u') ORDER BY 1, 2"
```

## Run the tests

```bash
backend/.venv/bin/pytest backend
```

This runs the `aether_bench` tests (fake executables, synthetic fixtures
labelled `TEST_FIXTURE`), the API tests (FastAPI `TestClient` with a synthetic
metadata provider) and two integration tests against the real C++ binaries.
The integration tests are skipped when the engine is not built; set
`AETHER_REQUIRE_INTEGRATION=1` to make that a failure instead.

`tests/api/test_benchmark_persistence.py` (fake engine, SYNTHETIC reports) and
the real-engine HTTP test commit to the test database described below and
delete its rows first; they never touch `aether_x_dev`.

The database unit tests need no server. `tests/test_database_integration.py`
runs read-only checks against the real local PostgreSQL and is skipped when it
is unreachable; `AETHER_REQUIRE_DATABASE=1` makes that a failure.

`tests/test_db_schema.py` tests the schema, constraints, relationships and
migrations against a separate database (`AETHER_TEST_DB_NAME`, default
`aether_x_test`; the name must end in `_test` because the tests rebuild its
schema). It needs that database to exist with the same grants as
`aether_x_dev` and a matching `~/.pgpass` line.

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
