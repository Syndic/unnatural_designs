"""Holds the CodeQL extraction report to the answers a green job must not be allowed to hide.

The report exists because "the analysis succeeded" and "the analysis read the code" are different
claims, and CodeQL only ever makes the first. So the assertions here are about which failure shape
reaches which channel -- and, since the job now fails on all of them, that none of them can quietly
turn into a pass.

Every SARIF fixture is the shape a real Security run produced, because three of the assumptions
this file started from were wrong and only the runs said so. The extraction-error diagnostics carry
**no location**, so nothing can attribute them to a file. `cli/expected-extracted-files` is skipped
on pull requests, so the expected set is read from the tree here instead. And a cached overlay
database let one run's diagnostics reappear in another's results, which is why the job pins
`CODEQL_ACTION_OVERLAY_ANALYSIS=false`.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from meta.scripts.codeql_extraction_report import expected_files, main, read_sarif, report

_MODULE = "tools/network_infrastructure_maintenance"

# The seven extraction errors the Go extractor logged on every Security run from 2026-08-20 on,
# verbatim. Reproduced because a report that would not have caught this is not a report -- see
# .claude/CLAUDE.md "CodeQL runs as advanced setup". Note the absent location: that is the real
# shape, and it is why these can never be attributed to a file.
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

_SOURCE_FILES = [f"{_MODULE}/internal/audit/cables.go", f"{_MODULE}/cmd/netbox_audit/main.go"]


def located(rule_id: str, *uris: str) -> dict:
    """A notification carrying file locations, which is how the extracted set ships."""
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


def extracted_sarif(*paths: str) -> dict:
    return sarif(located("go/diagnostics/successfully-extracted-files", *paths))


def go_tree(root: Path, *relative_paths: str) -> None:
    """A minimal workspace: a go.work registering one module, and the files given inside it."""
    (root / "go.work").write_text(f"go 1.26.6\n\nuse ./{_MODULE}\n", encoding="utf-8")
    for relative in relative_paths:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("package x\n", encoding="utf-8")


def go_list_returning(root: Path, *relative_paths: str):
    """Mock `go list`, which is what decides the expected set.

    Mocked rather than run, for the reason test_check_no_cgo gives for the same call: the
    subprocess wrapper's argument construction and parsing are what this file can hold, and the
    real invocation against a fixture module belongs to CI.
    """
    stdout = "".join(f"{root / rel}\n" for rel in relative_paths)
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")
    return mock.patch.object(subprocess, "run", return_value=completed)


def run_report(document: dict, expected: list[str], language: str = "go"):
    extracted, problems, total = read_sarif(document)
    summary, commands = report(language, expected, extracted, problems, total)
    return "\n".join(summary), commands


def run_main(document: dict | str, tree: list[str], language: str = "go") -> tuple[int, str]:
    """Run the script end to end over a SARIF file and a real on-disk tree."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        go_tree(root, *tree)
        sarif_path = root / f"{language}.sarif"
        summary_path = root / "summary.md"
        sarif_path.write_text(
            document if isinstance(document, str) else json.dumps(document), encoding="utf-8"
        )
        with go_list_returning(root, *tree):
            code = main(
                [
                    str(sarif_path),
                    "--language",
                    language,
                    "--source-root",
                    str(root),
                    "--summary",
                    str(summary_path),
                ]
            )
        return code, summary_path.read_text(encoding="utf-8") if summary_path.exists() else ""


class ExpectedFilesTest(unittest.TestCase):
    """The expected set is whatever `go build` compiles, so it is asked rather than modelled."""

    def test_it_reports_what_go_list_compiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            go_tree(root)
            with go_list_returning(root, *_SOURCE_FILES):
                self.assertEqual(expected_files(root, "go"), sorted(_SOURCE_FILES))

    def test_it_asks_for_the_compiled_file_list(self):
        """`.GoFiles` is the compiler's own list: no test files, no testdata, no excluded builds."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            go_tree(root)
            with go_list_returning(root) as run:
                expected_files(root, "go")
            argv = run.call_args.args[0]
            self.assertEqual(argv[:3], ["go", "list", "-f"])
            self.assertIn(".GoFiles", argv[3])
            self.assertIn("./...", argv)

    def test_conventions_the_go_tool_applies_are_not_reimplemented(self):
        """testdata/, _-prefixed dirs and build-constrained files are go list's job, not ours.

        Modelling them here would mean an exclusion per convention, and each new one would be
        indistinguishable from widening the gate to quiet a red run.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            go_tree(
                root,
                *_SOURCE_FILES,
                f"{_MODULE}/internal/audit/testdata/sample.go",
                f"{_MODULE}/internal/audit/_scratch/x.go",
                f"{_MODULE}/internal/audit/helpers_windows.go",
                f"{_MODULE}/internal/audit/audit_test.go",
            )
            with go_list_returning(root, *_SOURCE_FILES):
                self.assertEqual(expected_files(root, "go"), sorted(_SOURCE_FILES))

    def test_a_failing_go_list_is_not_a_pass(self):
        """An expected set nobody could compute is a check that did not run."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            go_tree(root)
            with (
                mock.patch.object(
                    subprocess, "run", side_effect=subprocess.CalledProcessError(1, "go list")
                ),
                self.assertRaises(subprocess.CalledProcessError),
            ):
                expected_files(root, "go")

    def test_unbuilt_languages_are_not_asked(self):
        """`build-mode: none` offers every matching file, so there is nothing to compare."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            go_tree(root)
            with mock.patch.object(subprocess, "run") as run:
                self.assertEqual(expected_files(root, "python"), [])
            run.assert_not_called()


class ReadSarifTest(unittest.TestCase):
    def test_it_separates_extracted_files_from_problems(self):
        document = sarif(
            located("go/diagnostics/successfully-extracted-files", *_SOURCE_FILES),
            *(unlocated("error", m) for m in _GO_1_27_EXTRACTION_ERRORS),
        )
        extracted, problems, total = read_sarif(document)
        self.assertEqual(extracted, frozenset(_SOURCE_FILES))
        self.assertEqual(len(problems), 7)
        self.assertEqual(total, 8, "every notification is counted, including the extracted set")

    def test_a_sarif_without_invocations_yields_nothing(self):
        self.assertEqual(read_sarif({"runs": [{"results": []}]}), (frozenset(), [], 0))


class GoExtractorLagTest(unittest.TestCase):
    """The situation this was written for: seven stdlib errors, and a job that reported success."""

    def setUp(self):
        self.document = sarif(
            located("go/diagnostics/successfully-extracted-files", *_SOURCE_FILES),
            *(unlocated("error", m) for m in _GO_1_27_EXTRACTION_ERRORS),
        )
        self.summary, self.commands = run_report(self.document, sorted(_SOURCE_FILES))

    def test_it_errors(self):
        self.assertEqual(len(self.commands), 1, self.commands)
        self.assertTrue(self.commands[0].startswith("::error "), self.commands[0])

    def test_the_error_is_titled(self):
        """The runner's own annotations arrive untitled; a title is what makes this findable."""
        self.assertIn("title=CodeQL go extraction is incomplete", self.commands[0])

    def test_the_summary_carries_every_error_the_extractor_reported(self):
        for message in _GO_1_27_EXTRACTION_ERRORS:
            self.assertIn(message, self.summary)

    def test_it_fails_the_job(self):
        """A partly-extracted analysis is indistinguishable from a clean one, so it cannot pass."""
        self.assertEqual(run_main(self.document, _SOURCE_FILES)[0], 1)

    def test_it_names_the_remedy_not_just_the_diagnosis(self):
        """Red for as long as the lag lasts; a gate nobody can act on gets removed."""
        for text in (self.summary, self.commands[0]):
            self.assertIn("drop the", text)
            self.assertIn("bundle", text)


class CoverageGateTest(unittest.TestCase):
    """The other failure: files in the tree that never reached the database."""

    def test_an_unextracted_source_file_fails_the_job(self):
        code, summary = run_main(extracted_sarif(_SOURCE_FILES[0]), _SOURCE_FILES)
        self.assertEqual(code, 1)
        self.assertIn(_SOURCE_FILES[1], summary)

    def test_the_failure_names_the_files(self):
        (command,) = run_report(extracted_sarif(), sorted(_SOURCE_FILES))[1]
        self.assertTrue(command.startswith("::error "), command)
        for path in _SOURCE_FILES:
            self.assertIn(path, command)

    def test_full_coverage_passes(self):
        code, summary = run_main(extracted_sarif(*_SOURCE_FILES), _SOURCE_FILES)
        self.assertEqual(code, 0)
        self.assertIn(f"**{len(_SOURCE_FILES)} of {len(_SOURCE_FILES)}**", summary)

    def test_an_empty_expected_set_is_not_a_pass(self):
        """Nothing to compare against is a check that did not run, which is not a green light."""
        summary, commands = run_report(extracted_sarif(), [])
        self.assertTrue(commands[0].startswith("::error "), commands)
        self.assertIn("nothing to compare", summary.lower())
        self.assertEqual(run_main(extracted_sarif(), [])[0], 1)

    def test_both_failures_are_reported_separately(self):
        """Both fail the job, but they have different remedies, so they stay distinct."""
        document = sarif(
            located("go/diagnostics/successfully-extracted-files"),
            *(unlocated("error", m) for m in _GO_1_27_EXTRACTION_ERRORS),
        )
        _, commands = run_report(document, sorted(_SOURCE_FILES))
        self.assertEqual([c.split(" ", 1)[0] for c in commands], ["::error", "::error"])
        self.assertIn("did not extract this repo's code", commands[0])
        self.assertIn("extraction is incomplete", commands[1])


class LevelDefaultTest(unittest.TestCase):
    """SARIF defaults an omitted `level` to "warning"; reading it as None fails the gate open."""

    def setUp(self):
        self.document = sarif(
            located("go/diagnostics/successfully-extracted-files", *_SOURCE_FILES),
            {
                "message": {"text": "Extraction failed with error something real"},
                "descriptor": {"id": "go/diagnostics/extraction-errors"},
            },
        )

    def test_a_notification_without_a_level_is_still_a_problem(self):
        _, problems, total = read_sarif(self.document)
        self.assertEqual(len(problems), 1, "an omitted level defaults to warning, not to nothing")
        self.assertEqual(problems[0].level, "warning")
        self.assertEqual(total, 2)

    def test_it_fails_the_job(self):
        """The non-vacuity path does not catch this: `total` is 2, so the SARIF is not blind."""
        code, summary = run_main(self.document, _SOURCE_FILES)
        self.assertEqual(code, 1)
        self.assertNotIn("Clean", summary)


class NonExtractionDiagnosticTest(unittest.TestCase):
    """Everything the bundle reports fails, but only extraction failures get the lag remedy."""

    def setUp(self):
        self.document = sarif(
            located("py/diagnostics/successfully-extracted-files", "meta/scripts/x.py"),
            unlocated("warning", "Deprecated flag --foo", "cli/deprecated-flag"),
        )

    def test_it_still_fails(self):
        self.assertEqual(run_main(self.document, _SOURCE_FILES, language="python")[0], 1)

    def test_it_does_not_name_a_remedy_it_cannot_support(self):
        """There is no pinned toolchain on the python row, so "drop the bump" is unactionable."""
        summary, commands = run_report(self.document, [], language="python")
        (command,) = commands
        self.assertNotIn("drop the bump", command)
        self.assertNotIn("drop the toolchain bump", summary)
        self.assertIn("reported a diagnostic", command)

    def test_extraction_failures_still_get_the_remedy(self):
        document = sarif(
            located("go/diagnostics/successfully-extracted-files", *_SOURCE_FILES),
            unlocated("error", "Extraction failed with error boom"),
        )
        _, (command,) = run_report(document, sorted(_SOURCE_FILES))
        self.assertIn("drop the bump", command)


class EmptyExpectedSetStillReportsProblemsTest(unittest.TestCase):
    """A broken go.work must not bury the extractor's diagnostics back in the raw log."""

    def setUp(self):
        self.document = sarif(
            located("go/diagnostics/successfully-extracted-files"),
            *(unlocated("error", m) for m in _GO_1_27_EXTRACTION_ERRORS),
        )

    def test_both_failures_are_reported(self):
        commands = run_report(self.document, [])[1]
        titles = [c.split("::")[1] for c in commands]
        self.assertEqual(len(commands), 2, titles)
        self.assertTrue(any("coverage could not be checked" in t for t in titles), titles)
        self.assertTrue(any("extraction is incomplete" in t for t in titles), titles)

    def test_the_extraction_errors_reach_the_summary(self):
        summary, _ = run_report(self.document, [])
        for message in _GO_1_27_EXTRACTION_ERRORS:
            self.assertIn(message, summary)


class UnbuiltLanguageTest(unittest.TestCase):
    """Python and Actions are not gated on coverage, and must not invent a gap for it."""

    def setUp(self):
        self.document = sarif(
            located("py/diagnostics/successfully-extracted-files", "meta/scripts/check_go_work.py")
        )

    def test_it_does_not_fail(self):
        self.assertEqual(run_main(self.document, _SOURCE_FILES, language="python")[0], 0)

    def test_it_says_why_coverage_is_not_gated(self):
        summary, commands = run_report(self.document, [], language="python")
        self.assertIn("build-mode: none", summary)
        self.assertEqual(commands, [])


class BlindRunTest(unittest.TestCase):
    """No diagnostics is not the same claim as no problems, and now fails rather than warning."""

    def test_it_fails(self):
        (command,) = run_report(sarif(), sorted(_SOURCE_FILES))[1]
        self.assertTrue(command.startswith("::error "), command)
        self.assertEqual(run_main(sarif(), _SOURCE_FILES)[0], 1)

    def test_it_points_at_the_pinned_env_var(self):
        """The input is pinned on the job, so its absence is a configuration fault, not weather."""
        (command,) = run_report(sarif(), sorted(_SOURCE_FILES))[1]
        self.assertIn("CODEQL_ACTION_EXPORT_DIAGNOSTICS", command)

    def test_it_does_not_read_as_clean(self):
        self.assertNotIn("Clean", run_report(sarif(), sorted(_SOURCE_FILES))[0])


class UnreadableSarifTest(unittest.TestCase):
    """Unlike every case above, this one is our own wiring and so is always ours to fix."""

    def test_malformed_sarif_fails(self):
        self.assertEqual(run_main("{not json", _SOURCE_FILES)[0], 1)

    def test_missing_sarif_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            go_tree(root)
            self.assertEqual(
                main([str(root / "absent.sarif"), "--source-root", str(root)]),
                1,
            )


class SummaryFormattingTest(unittest.TestCase):
    def test_multi_line_messages_stay_on_one_table_row(self):
        """The extractor lists one error per line; a newline in a cell ends the markdown table."""
        document = sarif(
            located("go/diagnostics/successfully-extracted-files", *_SOURCE_FILES),
            unlocated("error", "first error\nsecond error"),
        )
        summary, _ = run_report(document, sorted(_SOURCE_FILES))
        rows = [line for line in summary.splitlines() if line.startswith("| error")]
        self.assertEqual(len(rows), 1)
        self.assertIn("first error second error", rows[0])

    def test_the_report_is_titled_with_the_language(self):
        self.assertIn("## CodeQL extraction: python", run_report(sarif(), [], language="python")[0])


if __name__ == "__main__":
    unittest.main()
