"""Holds the CodeQL extraction report to the two answers a green job must not hide.

The report exists because "the analysis succeeded" and "the analysis read the code" are different
claims, and CodeQL only ever makes the first one. So the assertions here are about which of the
two failure shapes reaches which channel: our own code going unextracted has to fail the job,
someone else's code has to warn without failing, and a run whose diagnostics are missing entirely
has to say so rather than read as clean.

The last one is the trap worth naming. A report that treats "no diagnostics" as "no problems"
inherits exactly the blindness it was written to remove -- and the diagnostics are opt-in behind a
feature flag GitHub resolves per run, so the empty case is reachable without any change here.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from meta.scripts.codeql_extraction_report import Diagnostic, main, read_diagnostics, report

# Where the runner unpacks the pinned Go toolchain. Every path below is under it, which is the
# property that makes them someone else's code as far as this repo is concerned.
_GOROOT = "/opt/hostedtoolcache/go/1.27.0/x64/src"

# The five packages the Go extractor could not read on every Security run from 2026-08-20 on,
# with the type-checker errors it logged for them. Reproduced here because a report that would
# not have caught this is not a report -- see .claude/CLAUDE.md "CodeQL runs as advanced setup".
_TOO_NEW = "file requires newer Go version go1.27"
_GO_1_27_EXTRACTION_FAILURES = [
    ("internal/poll/splice_linux.go", "unknown field rfd in struct literal"),
    ("math/rand/v2/rand.go", "method must have no type parameters"),
    ("vendor/golang.org/x/net/idna/tables1_27.go", _TOO_NEW),
    ("vendor/golang.org/x/text/unicode/bidi/tables1_27.go", _TOO_NEW),
    ("vendor/golang.org/x/text/unicode/norm/tables1_27.go", _TOO_NEW),
]


def notification(level: str, uri: str | None, message: str, rule_id: str = "go/diagnostics/x"):
    body: dict = {
        "level": level,
        "message": {"text": message},
        "descriptor": {"id": rule_id},
    }
    if uri is not None:
        body["locations"] = [{"physicalLocation": {"artifactLocation": {"uri": uri}}}]
    return body


def sarif(*notifications: dict) -> dict:
    return {"runs": [{"invocations": [{"toolExecutionNotifications": list(notifications)}]}]}


def go_sarif_as_shipped() -> dict:
    """The real run's shape: one informational note plus the five unreadable stdlib packages."""
    return sarif(
        notification(
            "note",
            None,
            "Extracted 35 files",
            "go/diagnostics/successfully-extracted-files",
        ),
        *(
            notification("error", f"{_GOROOT}/{path}", message, "go/diagnostics/extraction-errors")
            for path, message in _GO_1_27_EXTRACTION_FAILURES
        ),
    )


def commands_for(sarif_document: dict, language: str = "go") -> list[str]:
    diagnostics, total = read_diagnostics(sarif_document)
    return report(language, diagnostics, total)[1]


def summary_for(sarif_document: dict, language: str = "go") -> str:
    diagnostics, total = read_diagnostics(sarif_document)
    return "\n".join(report(language, diagnostics, total)[0])


def run_main(sarif_document: dict | str) -> tuple[int, str]:
    """Run the script over a SARIF file on disk; returns its exit code and the summary written."""
    with tempfile.TemporaryDirectory() as tmp:
        sarif_path = Path(tmp) / "go.sarif"
        summary_path = Path(tmp) / "summary.md"
        sarif_path.write_text(
            sarif_document if isinstance(sarif_document, str) else json.dumps(sarif_document),
            encoding="utf-8",
        )
        code = main([str(sarif_path), "--language", "go", "--summary", str(summary_path)])
        return code, summary_path.read_text(encoding="utf-8") if summary_path.exists() else ""


class ReadDiagnosticsTest(unittest.TestCase):
    def test_counts_every_notification_but_reports_only_error_and_warning(self):
        diagnostics, total = read_diagnostics(
            sarif(
                notification("note", None, "Extracted 35 files"),
                notification("warning", "/usr/lib/go/src/net/http.go", "gave up"),
                notification("error", "internal/audit/cables.go", "gave up"),
            )
        )
        self.assertEqual(total, 3, "the note is the evidence the diagnostics ran; count it")
        self.assertEqual([d.level for d in diagnostics], ["warning", "error"])

    def test_a_sarif_without_invocations_reports_nothing_and_counts_nothing(self):
        self.assertEqual(read_diagnostics({"runs": [{"results": []}]}), ([], 0))

    def test_message_and_rule_id_survive(self):
        (diagnostic,), _ = read_diagnostics(
            sarif(notification("error", "main.go", "  boom  ", "go/diagnostics/extraction-errors"))
        )
        self.assertEqual(diagnostic.message, "boom")
        self.assertEqual(diagnostic.rule_id, "go/diagnostics/extraction-errors")


class FirstPartyTest(unittest.TestCase):
    """Whose code failed is the whole gate, so the URI test is the load-bearing line."""

    def first_party(self, uri: str | None) -> bool:
        (diagnostic,), _ = read_diagnostics(sarif(notification("error", uri, "gave up")))
        return diagnostic.first_party

    def test_repo_relative_paths_are_ours(self):
        self.assertTrue(self.first_party("tools/network_infrastructure_maintenance/main.go"))

    def test_absolute_toolchain_paths_are_not(self):
        self.assertFalse(self.first_party(f"{_GOROOT}/internal/poll.go"))

    def test_file_urls_are_not(self):
        self.assertFalse(self.first_party(f"file://{_GOROOT}/rand.go"))

    def test_a_diagnostic_with_no_location_is_not_claimed_as_ours(self):
        """Nothing to point a reader at, so it is reported without failing the job."""
        self.assertFalse(self.first_party(None))


class GoExtractorLagTest(unittest.TestCase):
    """The situation the report was written for: five stdlib packages, and a green job."""

    def setUp(self):
        self.document = go_sarif_as_shipped()

    def test_it_warns(self):
        commands = commands_for(self.document)
        self.assertEqual(len(commands), 1, commands)
        self.assertTrue(commands[0].startswith("::warning "), commands[0])

    def test_the_warning_names_every_unreadable_package(self):
        (command,) = commands_for(self.document)
        for path, _ in _GO_1_27_EXTRACTION_FAILURES:
            self.assertIn(f"{_GOROOT}/{path}", command)

    def test_the_summary_carries_the_type_checker_message(self):
        summary = summary_for(self.document)
        for _, message in _GO_1_27_EXTRACTION_FAILURES:
            self.assertIn(message, summary)

    def test_it_does_not_fail_the_job(self):
        """Upstream lag on GitHub's schedule; failing here blocks merges nobody here can unblock."""
        code, _ = run_main(self.document)
        self.assertEqual(code, 0)


class FirstPartyFailsTheJobTest(unittest.TestCase):
    def setUp(self):
        self.document = sarif(
            notification("note", None, "Extracted 30 files"),
            notification("error", "internal/audit/cables.go", "gave up on this file"),
        )

    def test_it_errors(self):
        (command,) = commands_for(self.document)
        self.assertTrue(command.startswith("::error "), command)

    def test_it_fails_the_job(self):
        code, _ = run_main(self.document)
        self.assertEqual(code, 1)

    def test_first_party_and_upstream_are_reported_separately(self):
        document = sarif(
            notification("error", "internal/audit/cables.go", "gave up"),
            notification("error", f"{_GOROOT}/rand.go", "gave up"),
        )
        levels = [command.split(" ", 1)[0] for command in commands_for(document)]
        self.assertEqual(levels, ["::warning", "::error"])


class CleanRunTest(unittest.TestCase):
    """The other half of the proof: a clean extraction must annotate nothing at all."""

    def setUp(self):
        self.document = sarif(
            notification(
                "note",
                None,
                "Extracted 35 files",
                "go/diagnostics/successfully-extracted-files",
            )
        )

    def test_no_annotations(self):
        self.assertEqual(commands_for(self.document), [])

    def test_summary_says_so(self):
        self.assertIn("Clean", summary_for(self.document))

    def test_exit_zero(self):
        code, summary = run_main(self.document)
        self.assertEqual(code, 0)
        self.assertIn("Clean", summary)


class BlindRunTest(unittest.TestCase):
    """No diagnostics is not the same claim as no problems, and must not be reported as one."""

    def test_it_warns_that_it_could_not_check(self):
        (command,) = commands_for(sarif())
        self.assertTrue(command.startswith("::warning "), command)
        self.assertIn("unverified", command)

    def test_it_does_not_read_as_clean(self):
        self.assertNotIn("Clean", summary_for(sarif()))

    def test_it_does_not_fail_the_job(self):
        """The flag that governs this is GitHub's, so a failure here is an outage we can't end."""
        code, _ = run_main(sarif())
        self.assertEqual(code, 0)


class UnreadableSarifTest(unittest.TestCase):
    """Unlike every case above, this one is our own wiring and so is always ours to fix."""

    def test_malformed_sarif_fails(self):
        code, _ = run_main("{not json")
        self.assertEqual(code, 1)

    def test_missing_sarif_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(main([str(Path(tmp) / "absent.sarif")]), 1)


class SummaryFormattingTest(unittest.TestCase):
    def test_multi_line_messages_stay_on_one_table_row(self):
        """The extractor lists one error per line; a newline in a cell ends the markdown table."""
        summary = summary_for(
            sarif(notification("error", "/usr/lib/go/src/x.go", "first error\nsecond error"))
        )
        rows = [line for line in summary.splitlines() if line.startswith("|")]
        self.assertTrue(any("first error second error" in row for row in rows), summary)

    def test_the_report_is_titled_with_the_language(self):
        self.assertIn("## CodeQL extraction: python", summary_for(sarif(), language="python"))


class DiagnosticTest(unittest.TestCase):
    def test_where_names_the_absence_of_a_location(self):
        self.assertEqual(Diagnostic("error", "x", (), "").where, "(no location)")


if __name__ == "__main__":
    unittest.main()
