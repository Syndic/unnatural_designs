"""Holds the CodeQL extraction report to the answers a green job must not be allowed to hide.

The report exists because "the analysis succeeded" and "the analysis read the code" are different
claims, and CodeQL only ever makes the first. So the assertions here are about which failure shape
reaches which channel: this repo's own code going unextracted fails the job, an extractor that gave
up on someone else's code warns without failing, and a SARIF with no diagnostics at all says so
rather than reading as clean.

Every fixture below is the shape a real Security run produced (32924314733), because two of the
assumptions this file started from were wrong and only the run said so. The extraction-error
diagnostics carry **no location**, so nothing can attribute them to a file -- which is why the gate
is built on the coverage sets instead. And the expected-files baseline is published for `go` and
`python` but not for `actions`, so "no baseline" is a live case, not a hypothetical.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from meta.scripts.codeql_extraction_report import Coverage, main, read_sarif, report

_MODULE = "tools/network_infrastructure_maintenance"

# The seven extraction errors the Go extractor logged on every Security run from 2026-08-20 on,
# verbatim. Reproduced because a report that would not have caught this is not a report -- see
# .claude/CLAUDE.md "CodeQL runs as advanced setup". Note the absent location: that is the real
# shape, and it is why these can only ever warn.
_GO_1_27_EXTRACTION_ERRORS = [
    "Extraction failed with error unknown field rfd in struct literal of type splicePipe",
    "Extraction failed with error unknown field wfd in struct literal of type splicePipe",
    "Extraction failed with error This application uses version go1.26 of the source-processing"
    " packages but runs version go1.27 of 'go list'.",
    "Extraction failed with error method must have no type parameters",
    "Extraction failed with error Int (function) is not a type",
    "Extraction failed with error too many arguments in call to Int have (uint64) want ()",
    "Extraction failed with error file requires newer Go version go1.27 (application built with"
    " go1.26)",
]

# The nine _test.go files `go build` does not compile, so `build-mode: autobuild` never extracts
# them. They are the entire difference between the 44 expected and the 35 extracted.
_TEST_FILES = [
    f"{_MODULE}/cmd/netbox_audit/config_docs_test.go",
    f"{_MODULE}/cmd/netbox_audit/main_test.go",
    f"{_MODULE}/internal/audit/audit_test.go",
    f"{_MODULE}/internal/netbox/client_test.go",
    f"{_MODULE}/internal/netbox/snapshot_test.go",
    f"{_MODULE}/internal/shared/terminal_test.go",
    f"{_MODULE}/internal/ui/progress/plain_test.go",
    f"{_MODULE}/internal/ui/progress/reporter_test.go",
    f"{_MODULE}/internal/ui/progress/rich_test.go",
]
_SOURCE_FILES = [f"{_MODULE}/internal/audit/cables.go", f"{_MODULE}/cmd/netbox_audit/main.go"]


def located(rule_id: str, *uris: str) -> dict:
    """A `note`-level notification carrying file locations, which is how the coverage sets ship."""
    return {
        "level": "none",
        "message": {"text": ""},
        "descriptor": {"id": rule_id},
        "locations": [
            {"physicalLocation": {"artifactLocation": {"uri": uri, "uriBaseId": "%SRCROOT%"}}}
            for uri in uris
        ],
    }


def unlocated(level: str, message: str, rule_id: str = "go/diagnostics/extraction-errors") -> dict:
    """An extraction problem. No `locations` key at all -- verified against the real SARIF."""
    return {"level": level, "message": {"text": message}, "descriptor": {"id": rule_id}}


def sarif(*notifications: dict) -> dict:
    return {"runs": [{"invocations": [{"toolExecutionNotifications": list(notifications)}]}]}


def go_sarif_as_shipped() -> dict:
    """The real run: 44 expected, 35 extracted, and seven unreadable stdlib packages."""
    return sarif(
        located("cli/expected-extracted-files/go", *_SOURCE_FILES, *_TEST_FILES),
        located("go/diagnostics/successfully-extracted-files", *_SOURCE_FILES, f"{_MODULE}/go.mod"),
        *(unlocated("error", message) for message in _GO_1_27_EXTRACTION_ERRORS),
    )


def run_report(document: dict, language: str = "go") -> tuple[str, list[str]]:
    coverage, problems, total = read_sarif(document, language)
    summary, commands = report(language, coverage, problems, total)
    return "\n".join(summary), commands


def run_main(document: dict | str, language: str = "go") -> tuple[int, str]:
    """Run the script over a SARIF file on disk; returns its exit code and the summary written."""
    with tempfile.TemporaryDirectory() as tmp:
        sarif_path = Path(tmp) / f"{language}.sarif"
        summary_path = Path(tmp) / "summary.md"
        sarif_path.write_text(
            document if isinstance(document, str) else json.dumps(document), encoding="utf-8"
        )
        code = main([str(sarif_path), "--language", language, "--summary", str(summary_path)])
        return code, summary_path.read_text(encoding="utf-8") if summary_path.exists() else ""


class ReadSarifTest(unittest.TestCase):
    def test_it_separates_the_three_notification_families(self):
        coverage, problems, total = read_sarif(go_sarif_as_shipped(), "go")
        self.assertEqual(len(coverage.expected), 11)
        self.assertEqual(len(coverage.extracted), 3)
        self.assertEqual(len(problems), 7)
        self.assertEqual(total, 9, "every notification is counted, including the coverage sets")

    def test_the_baseline_is_read_for_this_language_only(self):
        """Each SARIF carries `cli/expected-extracted-files/*` for languages it did not analyse."""
        document = sarif(
            located("cli/expected-extracted-files/go", "a.go"),
            located("cli/expected-extracted-files/python", "b.py", "c.py"),
        )
        self.assertEqual(read_sarif(document, "go")[0].expected, ("a.go",))
        self.assertEqual(len(read_sarif(document, "python")[0].expected), 2)

    def test_a_sarif_without_invocations_yields_nothing(self):
        coverage, problems, total = read_sarif({"runs": [{"results": []}]}, "go")
        self.assertEqual((coverage.expected, problems, total), ((), [], 0))


class GoExtractorLagTest(unittest.TestCase):
    """The situation this was written for: seven stdlib errors, and a job that reported success."""

    def setUp(self):
        self.summary, self.commands = run_report(go_sarif_as_shipped())

    def test_it_warns(self):
        self.assertEqual(len(self.commands), 1, self.commands)
        self.assertTrue(self.commands[0].startswith("::warning "), self.commands[0])

    def test_the_warning_is_titled(self):
        """The runner's own annotations arrive untitled; a title is what makes this findable."""
        self.assertIn("title=CodeQL go extraction degraded", self.commands[0])

    def test_the_summary_carries_every_error_the_extractor_reported(self):
        for message in _GO_1_27_EXTRACTION_ERRORS:
            self.assertIn(message.replace("\n", " "), self.summary)

    def test_it_does_not_fail_the_job(self):
        """Upstream lag on GitHub's schedule; failing here blocks merges nobody here can unblock."""
        self.assertEqual(run_main(go_sarif_as_shipped())[0], 0)

    def test_it_does_not_read_as_clean(self):
        self.assertNotIn("Clean", self.summary)


class CoverageGateTest(unittest.TestCase):
    """The one hard failure: files this repo owns that never reached the database."""

    def test_go_test_files_are_not_a_coverage_gap(self):
        """`go build` does not compile them, so autobuild never extracts them. 35 of 44 is full."""
        summary, commands = run_report(go_sarif_as_shipped())
        self.assertEqual([c for c in commands if c.startswith("::error ")], [])
        self.assertIn(f"**{len(_SOURCE_FILES)} of 11**", summary)

    def test_an_unextracted_source_file_fails_the_job(self):
        document = sarif(
            located("cli/expected-extracted-files/go", *_SOURCE_FILES),
            located("go/diagnostics/successfully-extracted-files", _SOURCE_FILES[0]),
        )
        code, summary = run_main(document)
        self.assertEqual(code, 1)
        self.assertIn(_SOURCE_FILES[1], summary)

    def test_the_failure_names_the_files(self):
        document = sarif(
            located("cli/expected-extracted-files/go", *_SOURCE_FILES),
            located("go/diagnostics/successfully-extracted-files"),
        )
        (command,) = run_report(document)[1]
        self.assertTrue(command.startswith("::error "), command)
        for path in _SOURCE_FILES:
            self.assertIn(path, command)

    def test_the_exclusion_is_go_only(self):
        """Nothing else builds, so nothing else has a compile set to be outside of."""
        coverage = Coverage(expected=("a_test.go",), extracted=frozenset())
        self.assertEqual(coverage.unextracted("go"), [])
        self.assertEqual(coverage.unextracted("python"), ["a_test.go"])

    def test_both_failures_can_be_reported_at_once(self):
        document = sarif(
            located("cli/expected-extracted-files/go", *_SOURCE_FILES),
            located("go/diagnostics/successfully-extracted-files"),
            *(unlocated("error", message) for message in _GO_1_27_EXTRACTION_ERRORS),
        )
        levels = [command.split(" ", 1)[0] for command in run_report(document)[1]]
        self.assertEqual(levels, ["::error", "::warning"])


class MissingBaselineTest(unittest.TestCase):
    """Live case, not hypothetical: the `actions` row publishes no expected-files set."""

    def setUp(self):
        self.document = sarif(
            located("actions/diagnostics/successfully-extracted-files", ".github/workflows/ci.yml"),
            located("codeql-action/overlay-disabled"),
        )

    def test_it_says_coverage_was_not_checked(self):
        summary, _ = run_report(self.document, "actions")
        self.assertIn("no expected-files baseline", summary)

    def test_it_does_not_invent_a_gap(self):
        code, _ = run_main(self.document, "actions")
        self.assertEqual(code, 0)

    def test_it_does_not_annotate(self):
        """The `actions` row would otherwise carry this warning on every run, forever."""
        self.assertEqual(run_report(self.document, "actions")[1], [])

    def test_it_does_not_claim_the_coverage_it_did_not_check(self):
        """ "Clean" here would assert full coverage off a baseline this analysis never published."""
        self.assertNotIn("Clean", run_report(self.document, "actions")[0])


class CleanRunTest(unittest.TestCase):
    """The other half of the proof: a clean extraction must annotate nothing at all."""

    def setUp(self):
        self.document = sarif(
            located("cli/expected-extracted-files/go", *_SOURCE_FILES),
            located("go/diagnostics/successfully-extracted-files", *_SOURCE_FILES),
        )

    def test_no_annotations(self):
        self.assertEqual(run_report(self.document)[1], [])

    def test_summary_says_so(self):
        code, summary = run_main(self.document)
        self.assertEqual(code, 0)
        self.assertIn("Clean", summary)


class BlindRunTest(unittest.TestCase):
    """No diagnostics is not the same claim as no problems, and must not be reported as one."""

    def test_it_warns_that_it_could_not_check(self):
        (command,) = run_report(sarif())[1]
        self.assertTrue(command.startswith("::warning "), command)
        self.assertIn("unverified", command)

    def test_it_does_not_read_as_clean(self):
        self.assertNotIn("Clean", run_report(sarif())[0])

    def test_it_does_not_fail_the_job(self):
        """The flag that governs this is GitHub's, so a failure here is an outage we can't end."""
        self.assertEqual(run_main(sarif())[0], 0)


class UnreadableSarifTest(unittest.TestCase):
    """Unlike every case above, this one is our own wiring and so is always ours to fix."""

    def test_malformed_sarif_fails(self):
        self.assertEqual(run_main("{not json")[0], 1)

    def test_missing_sarif_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(main([str(Path(tmp) / "absent.sarif")]), 1)


class SummaryFormattingTest(unittest.TestCase):
    def test_multi_line_messages_stay_on_one_table_row(self):
        """The extractor lists one error per line; a newline in a cell ends the markdown table."""
        summary, _ = run_report(sarif(unlocated("error", "first error\nsecond error")))
        rows = [line for line in summary.splitlines() if line.startswith("| error")]
        self.assertEqual(len(rows), 1)
        self.assertIn("first error second error", rows[0])

    def test_the_report_is_titled_with_the_language(self):
        self.assertIn("## CodeQL extraction: python", run_report(sarif(), "python")[0])


if __name__ == "__main__":
    unittest.main()
