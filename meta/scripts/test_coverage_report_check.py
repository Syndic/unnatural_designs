"""Holds the coverage report check to the ways a green `bazel coverage` can have measured nothing.

Each failure the check exists for has a case here: a missing or empty report, a language with
test targets and no records, a language whose records hit no line, and a test rule kind the table
does not know. The last is the one most likely to be "fixed" by skipping it, so it is the one most
worth holding.
"""

import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from meta.scripts import coverage_report_check
from meta.scripts.coverage_report_check import Measured, findings, main, read_lcov, test_kinds

_GO_RECORD = "SF:tools/x/main.go\nDA:1,1\nDA:2,0\nLH:1\nLF:2\nend_of_record\n"
_PY_RECORD = "SF:meta/scripts/x.py\nDA:1,3\nLH:1\nLF:1\nend_of_record\n"
_PY_UNHIT = "SF:meta/scripts/x.py\nDA:1,0\nDA:2,0\nLH:0\nLF:2\nend_of_record\n"


class TestKindsTest(unittest.TestCase):
    def test_reads_the_kind_column(self):
        output = "py_test rule //meta/scripts:test_a\ngo_test rule //tools/x:x_test\n\n"
        self.assertEqual(test_kinds(output), {"py_test", "go_test"})


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
    def run_main(self, lcov_text, kinds_output="py_test rule //a:b\n", query_error=None):
        with tempfile.TemporaryDirectory() as tmp:
            lcov = Path(tmp) / "report.dat"
            if lcov_text is not None:
                lcov.write_text(lcov_text, encoding="utf-8")
            summary = Path(tmp) / "summary.md"
            result = subprocess.CompletedProcess([], 0, stdout=kinds_output, stderr="")
            run = mock.patch.object(
                coverage_report_check.subprocess,
                "run",
                side_effect=query_error,
                return_value=result,
            )
            out = io.StringIO()
            with (
                run,
                mock.patch.object(coverage_report_check, "workspace_root", return_value=Path(tmp)),
                contextlib.redirect_stdout(out),
            ):
                status = main([str(lcov), "--summary", str(summary)])
            written = summary.read_text(encoding="utf-8") if summary.exists() else ""
            return status, out.getvalue(), written

    def test_a_measured_report_passes_and_writes_the_summary(self):
        status, out, written = self.run_main(_PY_RECORD)
        self.assertEqual(status, 0)
        self.assertNotIn("::error", out)
        self.assertIn("Every language with test targets was measured.", written)

    def test_a_missing_report_fails(self):
        status, out, _ = self.run_main(None)
        self.assertEqual(status, 1)
        self.assertIn("::error title=Coverage report could not be read::", out)

    def test_an_empty_report_fails(self):
        status, out, _ = self.run_main("")
        self.assertEqual(status, 1)
        self.assertIn("::error title=Coverage did not measure the tree::", out)

    def test_a_failed_query_fails_rather_than_passing_unchecked(self):
        error = subprocess.CalledProcessError(2, ["bazel", "query"])
        status, out, _ = self.run_main(_PY_RECORD, query_error=error)
        self.assertEqual(status, 1)
        self.assertIn("::error title=Coverage expectation could not be computed::", out)


if __name__ == "__main__":
    unittest.main()
