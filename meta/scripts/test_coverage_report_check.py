"""Holds the coverage report check to the ways a green `bazel coverage` can have measured nothing.

Each failure the check exists for has a case here: a missing or empty report, a language with
test targets and no records, a language whose records hit no line, and a test rule kind the table
does not know. The last is the one most likely to be "fixed" by skipping it, so it is the one most
worth holding.
"""

import contextlib
import io
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from meta.scripts import coverage_report_check
from meta.scripts.coverage_report_check import (
    COMPATIBLE_LABEL_EXPR,
    MANUAL_TAG,
    TESTS_QUERY,
    Measured,
    compatible_labels,
    expected_kinds,
    findings,
    kinds_by_label,
    main,
    read_lcov,
)

_GO_RECORD = "SF:tools/x/main.go\nDA:1,1\nDA:2,0\nLH:1\nLF:2\nend_of_record\n"
_PY_RECORD = "SF:meta/scripts/x.py\nDA:1,3\nLH:1\nLF:1\nend_of_record\n"
_PY_UNHIT = "SF:meta/scripts/x.py\nDA:1,0\nDA:2,0\nLH:0\nLF:2\nend_of_record\n"


class ManualTagTest(unittest.TestCase):
    """`attr` matches a list's string form, so the pattern is tried against `[a, b]` here too."""

    def test_matches_manual_wherever_it_sits_in_the_list(self):
        for tags in ("[manual]", "[manual, x]", "[x, manual]", "[x, manual, y]"):
            with self.subTest(tags=tags):
                self.assertIsNotNone(re.search(MANUAL_TAG, tags))

    def test_does_not_match_a_tag_that_only_contains_manual(self):
        for tags in ("[manual-review]", "[not-manual]", "[x, manual.y]", "[manually]"):
            with self.subTest(tags=tags):
                self.assertIsNone(re.search(MANUAL_TAG, tags))

    def test_the_query_excludes_by_that_pattern(self):
        self.assertIn(f'attr(tags, "{MANUAL_TAG}", //...)', TESTS_QUERY)


class ExpectedKindsTest(unittest.TestCase):
    def test_reads_kind_per_label(self):
        output = "py_test rule //meta/scripts:test_a\ngo_test rule //tools/x:x_test\n\n"
        self.assertEqual(
            kinds_by_label(output),
            {"//meta/scripts:test_a": "py_test", "//tools/x:x_test": "go_test"},
        )

    def test_cquery_labels_are_matched_to_query_labels(self):
        self.assertEqual(
            compatible_labels("@@//meta/scripts:test_a\n\n"), {"//meta/scripts:test_a"}
        )

    def test_an_incompatible_test_contributes_no_kind(self):
        kinds = {"//a:py": "py_test", "//b:go": "go_test"}
        self.assertEqual(expected_kinds(kinds, {"//a:py"}), {"py_test"})


class ReadLcovTest(unittest.TestCase):
    def test_counts_files_and_hit_lines_per_extension(self):
        measured = read_lcov(_GO_RECORD + _PY_RECORD)
        self.assertEqual(measured, {".go": Measured(1, 1), ".py": Measured(1, 1)})

    def test_records_a_file_with_no_hit_lines(self):
        self.assertEqual(read_lcov(_PY_UNHIT), {".py": Measured(1, 0)})

    def test_an_empty_report_measures_nothing(self):
        self.assertEqual(read_lcov(""), {})


class FindingsTest(unittest.TestCase):
    def test_every_language_measured_is_clean(self):
        self.assertEqual(findings({"go_test", "py_test"}, read_lcov(_GO_RECORD + _PY_RECORD)), [])

    def test_a_language_with_tests_and_no_records_fails(self):
        problems = findings({"go_test", "py_test"}, read_lcov(_GO_RECORD))
        self.assertEqual(len(problems), 1)
        self.assertIn("no `.py` files", problems[0])

    def test_a_language_whose_records_hit_nothing_fails(self):
        problems = findings({"py_test"}, read_lcov(_PY_UNHIT))
        self.assertEqual(len(problems), 1)
        self.assertIn("no line in any of them was hit", problems[0])

    def test_an_unknown_test_kind_fails_closed(self):
        problems = findings({"py_test", "sh_test"}, read_lcov(_PY_RECORD))
        self.assertEqual(len(problems), 1)
        self.assertIn("`sh_test` is not in", problems[0])

    def test_a_kind_recorded_as_emitting_nothing_passes(self):
        with mock.patch.dict(coverage_report_check._NO_COVERAGE_KINDS, {"diff_test": "compares"}):
            self.assertEqual(findings({"py_test", "diff_test"}, read_lcov(_PY_RECORD)), [])

    def test_an_empty_report_fails(self):
        problems = findings({"py_test"}, {})
        self.assertTrue(any("no `SF:` records at all" in p for p in problems))

    def test_no_test_targets_fails(self):
        problems = findings(set(), read_lcov(_PY_RECORD))
        self.assertTrue(any("no test targets" in p for p in problems))


class MainTest(unittest.TestCase):
    def run_main(self, lcov_text, extra_args=(), query_error=None):
        """Runs `main` with both Bazel calls stubbed; returns status, stdout, summary, calls."""
        outputs = {"query": "py_test rule //a:b\n", "cquery": "@@//a:b\n"}

        def fake_run(argv, **_):
            if query_error is not None:
                raise query_error
            return subprocess.CompletedProcess(argv, 0, stdout=outputs[argv[1]], stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            lcov = Path(tmp) / "report.dat"
            if lcov_text is not None:
                lcov.write_text(lcov_text, encoding="utf-8")
            summary = Path(tmp) / "summary.md"
            out = io.StringIO()
            with (
                mock.patch.object(
                    coverage_report_check.subprocess, "run", side_effect=fake_run
                ) as run,
                mock.patch.object(coverage_report_check, "workspace_root", return_value=Path(tmp)),
                contextlib.redirect_stdout(out),
            ):
                status = main([str(lcov), "--summary", str(summary), *extra_args])
            written = summary.read_text(encoding="utf-8") if summary.exists() else ""
            calls = [call.args[0] for call in run.call_args_list]
            return status, out.getvalue(), written, calls

    def test_a_measured_report_passes_and_writes_the_summary(self):
        status, out, written, _ = self.run_main(_PY_RECORD)
        self.assertEqual(status, 0)
        self.assertNotIn("::error", out)
        self.assertIn("Every language with test targets was measured.", written)

    def test_asks_query_for_kinds_and_cquery_for_compatibility_with_the_runs_flags(self):
        flags = ["--config=ci", "--config=linux_x86_64"]
        _, _, _, calls = self.run_main(_PY_RECORD, extra_args=["--", *flags])
        self.assertEqual(
            calls,
            [
                ["bazel", "query", TESTS_QUERY, "--output=label_kind"],
                [
                    "bazel",
                    "cquery",
                    TESTS_QUERY,
                    *flags,
                    "--output=starlark",
                    f"--starlark:expr={COMPATIBLE_LABEL_EXPR}",
                ],
            ],
        )

    def test_a_missing_report_fails_and_says_so_in_the_summary(self):
        status, out, written, _ = self.run_main(None)
        self.assertEqual(status, 1)
        self.assertIn("::error title=Coverage report could not be read::", out)
        self.assertIn("Coverage report could not be read", written)

    def test_an_empty_report_fails(self):
        status, out, _, _ = self.run_main("")
        self.assertEqual(status, 1)
        self.assertIn("::error title=Coverage did not measure the tree::", out)

    def test_a_failed_query_fails_and_says_so_in_the_summary(self):
        error = subprocess.CalledProcessError(2, ["bazel", "query"])
        status, out, written, _ = self.run_main(_PY_RECORD, query_error=error)
        self.assertEqual(status, 1)
        self.assertIn("::error title=Coverage expectation could not be computed::", out)
        self.assertIn("Coverage expectation could not be computed", written)


if __name__ == "__main__":
    unittest.main()
