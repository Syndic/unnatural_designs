"""Covers the gate that makes a timeout-dropped file fail the Semgrep job.

The cases here are the ones that decide whether the gate is worth having. A gate that misreads
semgrep's `type` field, or that tolerates an error it has never seen, reports green on the failure
it exists to catch — which is the state #294 was already in, only with a check in front of it.

The `type` shapes are copied from real output rather than invented: `Timeout` arrives as a bare
string and `PartialParsing` as `["PartialParsing", [spans]]`, and code that assumes either one
alone breaks on the other.
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from meta.scripts import semgrep_scan_report as report


def _write(text):
    """A throwaway file holding `text`, returned by path."""
    path = Path(tempfile.mkdtemp()) / "semgrep.json"
    path.write_text(text, encoding="utf-8")
    return str(path)


def _report(*errors, **kwargs):
    body = {"errors": list(errors), "paths": {"scanned": kwargs.get("scanned", ["a.py"])}}
    return _write(json.dumps(body))


def _run(path):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = report.main([path])
    return code, buf.getvalue()


# Real shapes, as semgrep 1.176.0 emits them.
_TIMEOUT = {"type": "Timeout", "level": "warn", "path": "MODULE.bazel.lock"}
_PARTIAL = {
    "type": ["PartialParsing", [{"path": "meta/scripts/_workspace.py"}]],
    "level": "warn",
    "path": "meta/scripts/_workspace.py",
}


class ErrorTypeTest(unittest.TestCase):
    def test_a_bare_string_type_is_read(self):
        self.assertEqual(report.error_type(_TIMEOUT), "Timeout")

    def test_a_variant_type_is_read(self):
        # The list form is why `.startswith()` or set membership over the raw value is wrong.
        self.assertEqual(report.error_type(_PARTIAL), "PartialParsing")

    def test_a_missing_type_does_not_raise(self):
        self.assertEqual(report.error_type({"path": "x"}), "")


class GateTest(unittest.TestCase):
    def test_a_dropped_target_fails_and_is_named(self):
        code, out = _run(_report(_TIMEOUT))
        self.assertEqual(code, 1)
        self.assertIn("MODULE.bazel.lock", out)
        self.assertIn("gave up on this target", out)

    def test_an_accepted_error_does_not_fail(self):
        # PEP 758's unparenthesized `except`, which this repo has decided to live with.
        code, out = _run(_report(_PARTIAL))
        self.assertEqual(code, 0)
        self.assertIn("coverage intact", out)

    def test_an_accepted_error_is_still_reported(self):
        _, out = _run(_report(_PARTIAL))
        self.assertIn("_workspace.py", out)

    def test_a_clean_report_passes(self):
        code, out = _run(_report())
        self.assertEqual(code, 0)
        self.assertIn("coverage intact", out)

    def test_an_unknown_error_type_fails(self):
        # The whole point of the accepted-set being an allowlist: a type this gate has never seen
        # is one it cannot classify, and an unevaluated check is not a pass.
        code, _ = _run(_report({"type": "SomeFutureError", "path": "x.py"}))
        self.assertEqual(code, 1)

    def test_a_dropped_target_still_fails_beside_an_accepted_one(self):
        # The ordering that matters: a tolerated error must not mask a blocking one.
        code, out = _run(_report(_PARTIAL, _TIMEOUT))
        self.assertEqual(code, 1)
        self.assertIn("MODULE.bazel.lock", out)

    def test_every_gave_up_type_blocks(self):
        for kind in report._GAVE_UP_ON_A_TARGET:
            with self.subTest(kind=kind):
                code, _ = _run(_report({"type": kind, "path": "x"}))
                self.assertEqual(code, 1)


class CouldNotCheckTest(unittest.TestCase):
    def test_an_unreadable_report_fails(self):
        self.assertEqual(report.main(["/nonexistent/semgrep.json"]), 1)

    def test_a_report_without_an_errors_list_fails(self):
        # A schema change must not read as "no errors found".
        path = _write(json.dumps({"paths": {"scanned": []}}))
        self.assertEqual(report.main([path]), 1)

    def test_malformed_json_fails(self):
        self.assertEqual(report.main([_write("{not json")]), 1)


class SummaryTest(unittest.TestCase):
    def test_the_step_summary_is_appended_not_truncated(self):
        summary = Path(tempfile.mkdtemp()) / "summary.md"
        summary.write_text("existing\n", encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            report.main([_report(_TIMEOUT), "--summary", str(summary)])
        text = summary.read_text(encoding="utf-8")
        self.assertIn("existing", text)
        self.assertIn("MODULE.bazel.lock", text)


if __name__ == "__main__":
    unittest.main()
