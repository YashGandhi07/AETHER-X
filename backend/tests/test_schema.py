"""Validation tests for aether_bench.schema, using SYNTHETIC fixture reports."""

from __future__ import annotations

import json
import unittest

from aether_bench import BenchmarkOutputError, parse_report, parse_system_report

from .fixtures import synthetic_report, mutated


class ValidReportTests(unittest.TestCase):
    def test_parses_complete_report(self) -> None:
        report = parse_report(json.dumps(synthetic_report()))
        self.assertEqual(report.schema, "aether.cpu_profile.v2")
        self.assertEqual(len(report.records), 2)
        first = report.records[0]
        self.assertEqual(first.samples_ms, (1.0, 2.0, 3.0, 4.0))  # run order kept
        self.assertEqual(first.statistics.median, 2.5)
        self.assertEqual(report.system.core_types[0].physical_cpus, 2)

    def test_null_environment_fields_become_none(self) -> None:
        report = parse_report(json.dumps(synthetic_report()))
        self.assertIsNone(report.system.os_version)
        self.assertIsNone(report.system.memory_bytes)
        self.assertIsNone(report.build.cmake_cxx_flags)

    def test_single_sample_has_null_stddev(self) -> None:
        report = parse_report(json.dumps(synthetic_report()))
        self.assertIsNone(report.records[1].statistics.stddev)

    def test_unknown_extra_fields_are_ignored(self) -> None:
        text = mutated(synthetic_report(), "future_field", {"added": "later"})
        self.assertEqual(len(parse_report(text).records), 2)

    def test_empty_results_list_is_valid(self) -> None:
        self.assertEqual(parse_report(mutated(synthetic_report(), "results", [])).records, ())


class MalformedReportTests(unittest.TestCase):
    def assert_rejected(self, text: str, fragment: str) -> None:
        with self.assertRaises(BenchmarkOutputError) as caught:
            parse_report(text)
        self.assertIn(fragment, str(caught.exception))

    def test_empty_and_invalid_json(self) -> None:
        self.assert_rejected("", "empty")
        self.assert_rejected("   \n", "empty")
        self.assert_rejected("{not json", "not valid JSON")
        self.assert_rejected("[1, 2]", "expected object")

    def test_truncated_report(self) -> None:
        full = json.dumps(synthetic_report())
        self.assert_rejected(full[: len(full) // 2], "not valid JSON")

    def test_non_standard_constants_rejected(self) -> None:
        text = json.dumps(synthetic_report()).replace('"p95": 3.85', '"p95": NaN')
        self.assertIn("NaN", text)
        self.assert_rejected(text, "non-standard JSON constant NaN")

    def test_wrong_schema_or_unit(self) -> None:
        report = synthetic_report()
        self.assert_rejected(mutated(report, "schema", "aether.cpu_profile.v1"), "unsupported schema")
        self.assert_rejected(mutated(report, "time_unit", "s"), "$.time_unit")

    def test_missing_required_fields_name_their_path(self) -> None:
        report = synthetic_report()
        for path, expected in [
            ("run_id", "$.run_id"),
            ("environment.system", "$.environment.system"),
            ("environment.system.cpu_model", "$.environment.system.cpu_model"),
            ("environment.build.compiler_id", "$.environment.build.compiler_id"),
            ("results.0.statistics.p95", "$.results[0].statistics.p95"),
            ("results.0.samples", "$.results[0].samples"),
            ("results.1.config.measured_runs", "$.results[1].config.measured_runs"),
        ]:
            with self.subTest(path=path):
                self.assert_rejected(mutated(report, path, delete=True), f"{expected}: missing")

    def test_wrong_types(self) -> None:
        report = synthetic_report()
        for path, value, expected in [
            ("results.0.thread_count", True, "expected integer, got bool"),  # bool is not a count
            ("results.0.input_size", 1.5, "expected integer, got float"),
            ("results.0.samples.2", "3.0", "expected number, got str"),
            ("environment.system.logical_cpus", "10", "expected integer, got str"),
            ("environment.build.assertions_enabled", 0, "expected boolean, got int"),
            ("results", {}, "expected array"),
        ]:
            with self.subTest(path=path):
                self.assert_rejected(mutated(report, path, value), expected)

    def test_out_of_range_values(self) -> None:
        report = synthetic_report()
        self.assert_rejected(mutated(report, "results.0.samples.0", -1.0), "finite number >= 0")
        self.assert_rejected(mutated(report, "results.0.thread_count", 0), "must be >= 1")
        self.assert_rejected(mutated(report, "results.0.warmup_runs_executed", 1), "below the configured minimum")

    def test_identifier_formats(self) -> None:
        report = synthetic_report()
        self.assert_rejected(mutated(report, "run_id", "not-a-uuid"), "UUID v4")
        self.assert_rejected(mutated(report, "results.0.timestamp_utc", "2000-01-01 00:00:00"), "UTC timestamp")
        duplicate = mutated(report, "results.1.experiment_id", "00000000-0000-4000-8000-000000000001")
        self.assert_rejected(duplicate, "must all be distinct")

    def test_inconsistent_statistics_detected(self) -> None:
        # The reported values must agree with the raw samples they summarize.
        report = synthetic_report()
        self.assert_rejected(mutated(report, "results.0.statistics.median", 2.6), "statistics.median")
        self.assert_rejected(mutated(report, "results.0.statistics.p95", 4.0), "statistics.p95")
        self.assert_rejected(mutated(report, "results.0.statistics.stddev", 1.118), "statistics.stddev")
        self.assert_rejected(mutated(report, "results.1.statistics.stddev", 0.0), "null for a single sample")

    def test_sample_count_mismatch(self) -> None:
        self.assert_rejected(mutated(synthetic_report(), "results.0.statistics.count", 5), "has 4 entries")


class SystemReportTests(unittest.TestCase):
    """aether.system_info.v1 reuses the profile report's system/build validation."""

    @staticmethod
    def document() -> dict:
        environment = synthetic_report()["environment"]
        return {"schema": "aether.system_info.v1", "generated_at_utc": "2000-01-01T00:00:00Z",
                "system": environment["system"], "build": environment["build"]}

    def test_parses_valid_document(self) -> None:
        report = parse_system_report(json.dumps(self.document()))
        self.assertEqual(report.system.cpu_model, "TEST_FIXTURE CPU (not a real measurement)")
        self.assertIsNone(report.system.os_version)
        self.assertEqual(report.build.cxx_standard, "C++20")

    def test_rejects_wrong_schema_and_bad_fields(self) -> None:
        document = self.document()
        for text, fragment in [
            (mutated(document, "schema", "aether.cpu_profile.v2"), "unsupported schema"),
            (mutated(document, "system.logical_cpus", -1), "$.system.logical_cpus"),
            (mutated(document, "build", delete=True), "$.build: missing"),
            (mutated(document, "generated_at_utc", "now"), "UTC timestamp"),
        ]:
            with self.subTest(fragment=fragment), self.assertRaises(BenchmarkOutputError) as caught:
                parse_system_report(text)
            self.assertIn(fragment, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
