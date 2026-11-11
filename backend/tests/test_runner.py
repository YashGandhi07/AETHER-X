"""Process-handling tests for aether_bench.runner using FAKE profiler executables.

Each test installs a small Python script named `aether_cpu_profile` in a
temporary build directory, so every failure mode can be produced on demand
without touching the real C++ benchmark.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from aether_bench import (
    BenchmarkOutputError,
    BenchmarkProcessError,
    BenchmarkTimeoutError,
    ExecutableNotFoundError,
    BenchmarkSpec,
    resolve_executable,
    run_benchmark,
    run_cpu_profile,
    run_system_info,
)

from .fixtures import synthetic_report, write_fake_profiler

WRITE_VALID_REPORT = f"""
with open(report_path, "w") as f:
    f.write({json.dumps(json.dumps(synthetic_report()))})
print("fake profiler stdout")
print("fake profiler stderr", file=sys.stderr)
"""


class RunnerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="aether_runner_test_")
        self.build_dir = Path(self._temp.name) / "build"

    def tearDown(self) -> None:
        self._temp.cleanup()

    def install(self, body: str) -> None:
        write_fake_profiler(self.build_dir, body)

    def run_profile(self, timeout_s: float = 10.0):
        return run_cpu_profile(timeout_s=timeout_s, build_dir=self.build_dir)


class SuccessTests(RunnerTestCase):
    def test_valid_report_is_parsed_and_output_captured(self) -> None:
        self.install(WRITE_VALID_REPORT)
        run = self.run_profile()
        self.assertEqual(len(run.report.records), 2)
        self.assertEqual(run.stdout.strip(), "fake profiler stdout")
        self.assertEqual(run.stderr.strip(), "fake profiler stderr")
        self.assertGreater(run.wall_time_s, 0.0)

    def test_arguments_and_environment_are_controlled(self) -> None:
        # The fake records exactly what it received; the report write proves success.
        self.install(WRITE_VALID_REPORT + """
print(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(), "env": sorted(os.environ),
                  "stdin_is_empty": sys.stdin.read() == ""}))
""")
        received = json.loads(self.run_profile().stdout.splitlines()[-1])
        self.assertEqual(len(received["argv"]), 1, "exactly one argument: the report path")
        report_path = Path(received["argv"][0])
        self.assertEqual(report_path.name, "report.json")
        self.assertEqual(report_path.parent.resolve(), Path(received["cwd"]).resolve(),
                         "report path lives in the private working directory")
        self.assertFalse(report_path.exists(), "temporary directory removed afterwards")
        # macOS may add __CF_USER_TEXT_ENCODING for any process; nothing else is inherited.
        self.assertLessEqual(set(received["env"]) - {"__CF_USER_TEXT_ENCODING"}, {"PATH", "LC_ALL"})
        self.assertTrue(received["stdin_is_empty"])

    def test_shell_metacharacters_are_never_interpreted(self) -> None:
        # Paths travel as argv entries without a shell, so a name full of
        # shell syntax is just a name.
        tricky = Path(self._temp.name) / "build; touch PWNED $(id) `id`"
        write_fake_profiler(tricky, WRITE_VALID_REPORT)
        run = run_cpu_profile(timeout_s=10.0, build_dir=tricky)
        self.assertEqual(len(run.report.records), 2)
        self.assertFalse((Path(self._temp.name) / "PWNED").exists())


class FailureTests(RunnerTestCase):
    def test_missing_executable(self) -> None:
        with self.assertRaises(ExecutableNotFoundError) as caught:
            self.run_profile()
        self.assertIn("build it first", str(caught.exception))

    def test_non_executable_file(self) -> None:
        self.build_dir.mkdir(parents=True)
        (self.build_dir / "aether_cpu_profile").write_text("not a program")
        with self.assertRaises(ExecutableNotFoundError):
            self.run_profile()

    def test_unknown_executable_name_rejected(self) -> None:
        with self.assertRaises(ValueError):
            resolve_executable("/bin/sh", self.build_dir)

    def test_nonzero_exit_is_a_process_error(self) -> None:
        self.install("""
print("error: verification failed for vector_add", file=sys.stderr)
sys.exit(1)
""")
        with self.assertRaises(BenchmarkProcessError) as caught:
            self.run_profile()
        error = caught.exception
        self.assertEqual(error.returncode, 1)
        self.assertIn("verification failed", error.stderr)
        self.assertIn("exited with code 1: error: verification failed", str(error))

    def test_report_written_before_failing_exit_is_ignored(self) -> None:
        # A non-zero exit wins: a report from a failed run is never trusted.
        self.install(WRITE_VALID_REPORT + "sys.exit(1)\n")
        with self.assertRaises(BenchmarkProcessError):
            self.run_profile()

    def test_killed_by_signal(self) -> None:
        self.install("import signal\nos.kill(os.getpid(), signal.SIGKILL)\n")
        with self.assertRaises(BenchmarkProcessError) as caught:
            self.run_profile()
        self.assertLess(caught.exception.returncode, 0)
        self.assertIn("killed by SIGKILL", str(caught.exception))

    def test_timeout_kills_the_process(self) -> None:
        self.install("""
print("started", flush=True)
time.sleep(30)
""")
        with self.assertRaises(BenchmarkTimeoutError) as caught:
            self.run_profile(timeout_s=0.5)
        self.assertEqual(caught.exception.timeout_s, 0.5)
        self.assertIn("started", caught.exception.stdout)

    def test_success_exit_without_report(self) -> None:
        self.install("print('forgot to write the report')\n")
        with self.assertRaises(BenchmarkOutputError) as caught:
            self.run_profile()
        self.assertIn("wrote no report", str(caught.exception))

    def test_malformed_and_truncated_reports(self) -> None:
        for content, expected in [
            ("", "empty"),
            ("{\"schema\": ", "not valid JSON"),
            (json.dumps({"schema": "aether.cpu_profile.v2"}), "missing required field"),
        ]:
            with self.subTest(content=content[:20]):
                self.install(f"open(report_path, 'w').write({content!r})\n")
                with self.assertRaises(BenchmarkOutputError) as caught:
                    self.run_profile()
                self.assertIn(expected, str(caught.exception))

    def test_invalid_utf8_report(self) -> None:
        self.install("open(report_path, 'wb').write(b'\\xff\\xfe{}')\n")
        with self.assertRaises(BenchmarkOutputError) as caught:
            self.run_profile()
        self.assertIn("UTF-8", str(caught.exception))

    def test_invalid_timeouts_rejected_before_running(self) -> None:
        self.install(WRITE_VALID_REPORT)
        for timeout in (0, -1, float("inf"), float("nan")):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                self.run_profile(timeout_s=timeout)


class SystemInfoRunnerTests(RunnerTestCase):
    """run_system_info shares the hardened executor; output comes from stdout."""

    def install_system_info(self, body: str) -> None:
        script = write_fake_profiler(self.build_dir, body)
        script.rename(self.build_dir / "aether_system_info")

    def test_reads_document_from_stdout_without_arguments(self) -> None:
        environment = synthetic_report()["environment"]
        document = {"schema": "aether.system_info.v1", "generated_at_utc": "2000-01-01T00:00:00Z",
                    "system": environment["system"], "build": environment["build"]}
        self.install_system_info(f"""
assert len(sys.argv) == 1, "system_info takes no arguments"
print({json.dumps(json.dumps(document))})
""")
        report = run_system_info(timeout_s=10.0, build_dir=self.build_dir)
        self.assertEqual(report.system.os_family, "TEST_FIXTURE_os")

    def test_failures_use_the_same_error_types(self) -> None:
        with self.assertRaises(ExecutableNotFoundError):
            run_system_info(build_dir=self.build_dir)
        self.install_system_info("print('not json')\n")
        with self.assertRaises(BenchmarkOutputError):
            run_system_info(build_dir=self.build_dir)
        self.install_system_info("sys.exit(3)\n")
        with self.assertRaises(BenchmarkProcessError):
            run_system_info(build_dir=self.build_dir)
        self.install_system_info("time.sleep(30)\n")
        with self.assertRaises(BenchmarkTimeoutError):
            run_system_info(timeout_s=0.5, build_dir=self.build_dir)


class BenchmarkSpecTests(unittest.TestCase):
    def test_valid_spec_builds_exact_arguments(self) -> None:
        spec = BenchmarkSpec("vector_add_parallel", size=4096, threads=2, warmup_runs=1, measured_runs=5)
        self.assertEqual(spec.to_arguments(Path("/tmp/r.json")), [
            "--workload", "vector_add_parallel", "--size", "4096", "--threads", "2",
            "--warmup-runs", "1", "--measured-runs", "5", "--output", "/tmp/r.json"])

    def test_invalid_specs_rejected(self) -> None:
        for kwargs in [
            {"workload": "sh", "size": 1},
            {"workload": "vector_add; rm -rf /", "size": 1},
            {"workload": "vector_add", "size": -1},
            {"workload": "vector_add", "size": True},
            {"workload": "vector_add", "size": 4.0},
            {"workload": "vector_add", "size": "4"},
        ]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                BenchmarkSpec(**kwargs)


class RunBenchmarkTests(RunnerTestCase):
    SPEC = BenchmarkSpec("vector_add", size=4096, threads=1, warmup_runs=3, measured_runs=4)

    def install_run_benchmark(self, record_overrides: dict, records: int = 1, exit_code: int = 0) -> None:
        report = synthetic_report()
        record = report["results"][0]  # 4 samples
        record.update({"workload": "vector_add", "input_size": 4096, "thread_count": 1,
                       **record_overrides})
        report["results"] = [record] * records
        script = write_fake_profiler(self.build_dir, f"""
args = sys.argv[1:]
report_path = args[args.index("--output") + 1]
print(json.dumps(args))
with open(report_path, "w") as f:
    f.write({json.dumps(json.dumps(report))})
sys.exit({exit_code})
""")
        script.rename(self.build_dir / "aether_run_benchmark")

    def test_passes_exact_arguments_and_returns_report(self) -> None:
        self.install_run_benchmark({})
        run = run_benchmark(self.SPEC, timeout_s=10.0, build_dir=self.build_dir)
        argv = json.loads(run.stdout)
        self.assertEqual(argv[:10], ["--workload", "vector_add", "--size", "4096", "--threads", "1",
                                     "--warmup-runs", "3", "--measured-runs", "4"])
        self.assertEqual(argv[10], "--output")
        self.assertEqual(run.report.records[0].workload, "vector_add")

    def test_report_must_match_request(self) -> None:
        for overrides, records, expected in [
            ({"workload": "vector_add_parallel"}, 1, "workload"),
            ({"input_size": 8192}, 1, "input_size"),
            ({"thread_count": 4}, 1, "thread_count"),
            ({}, 0, "exactly 1 record"),
        ]:
            with self.subTest(expected=expected):
                self.install_run_benchmark(overrides, records)
                with self.assertRaises(BenchmarkOutputError) as caught:
                    run_benchmark(self.SPEC, timeout_s=10.0, build_dir=self.build_dir)
                self.assertIn(expected, str(caught.exception))

    def test_engine_rejection_is_a_process_error(self) -> None:
        self.install_run_benchmark({}, exit_code=2)
        with self.assertRaises(BenchmarkProcessError) as caught:
            run_benchmark(self.SPEC, timeout_s=10.0, build_dir=self.build_dir)
        self.assertEqual(caught.exception.returncode, 2)


if __name__ == "__main__":
    unittest.main()
