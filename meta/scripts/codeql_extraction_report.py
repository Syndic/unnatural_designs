#!/usr/bin/env python3
"""Reports what a CodeQL analysis actually read, and fails when it skipped this repo's own code.

CodeQL concludes success whenever the *analysis* completed, which is a different claim from
"the extractor read the code". The gap is easy to miss: the runner does turn the extractor's
`##[error]` lines into check-run annotations, but they arrive untitled, at paths the runner
mis-parsed out of the log text, and under a green check — so they read as noise rather than as
"part of the analysis did not happen". This script reads the SARIF the CLI wrote, where the same
facts are recorded properly as `toolExecutionNotifications`, and reports them as one titled
annotation plus a step summary.

Two independent things live in that SARIF, and both fail the job:

  - **File coverage.** `cli/expected-extracted-files/<language>` is the set of source-root files
    the CLI expected to extract; `<lang>/diagnostics/successfully-extracted-files` is what it got.
    A file in the first and not the second is this repo's own code going unanalysed.
  - **Extraction problems.** Error- and warning-level diagnostics from the extractor. These carry
    no location at all (verified against a real run), so they cannot be attributed to a file —
    today they are the toolchain's own standard library rather than ours.

The second used to warn, on the reasoning that this repo cannot hasten an upstream fix and a red
check would block unrelated merges. That trades the wrong way: a partly-extracted analysis is
indistinguishable from a clean one, so it is worse than a clean failure. What makes failing
affordable is quarantining the predictable cause — a Go minor bump landing ahead of the extractor
now arrives in its own Renovate PR, so the degradation is blocked there instead of reaching main.

Neither gate has a tolerance count, so there is no number to widen the day a run goes red.

Usage: ./meta/scripts/codeql_extraction_report.py <sarif-file> [--language <name>]
"""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

_EXPECTED_ID = "cli/expected-extracted-files/"
_EXTRACTED_ID_SUFFIX = "/diagnostics/successfully-extracted-files"

# Levels that mean the extractor gave up on something, as opposed to reporting what it did.
_PROBLEM_LEVELS = ("error", "warning")

# `build-mode: autobuild` builds Go with `go build`, which never compiles test files — so they are
# outside the compile set by construction, not by concession. This is the one exclusion the
# coverage gate makes, and widening it to quiet a red run is the move it exists to prevent.
_NOT_IN_THE_BUILD = {"go": lambda uri: uri.endswith("_test.go")}


@dataclass(frozen=True)
class Problem:
    """One error- or warning-level `toolExecutionNotification`."""

    level: str
    rule_id: str
    message: str


@dataclass(frozen=True)
class Coverage:
    """What the CLI expected to extract for one language, and what it did extract."""

    expected: tuple[str, ...]
    extracted: frozenset[str]

    def unextracted(self, language: str) -> list[str]:
        """Expected files that never made it into the database, build-mode exclusions aside."""
        excluded = _NOT_IN_THE_BUILD.get(language, lambda _: False)
        return sorted(f for f in self.expected if f not in self.extracted and not excluded(f))

    @property
    def has_baseline(self) -> bool:
        """False when this analysis published no expected-files set, so coverage is unknowable.

        Real and not hypothetical: the `actions` row publishes none, while `go` and `python` do.
        """
        return bool(self.expected)


def _locations(notification: dict) -> list[str]:
    return [
        uri
        for location in notification.get("locations", [])
        if (uri := location.get("physicalLocation", {}).get("artifactLocation", {}).get("uri"))
    ]


def read_sarif(sarif: dict, language: str) -> tuple[Coverage, list[Problem], int]:
    """One language's coverage and problems, plus the total notification count.

    That count is the non-vacuity signal: the CodeQL Action asks for diagnostics in SARIF based on
    a feature flag it resolves from GitHub, so a SARIF with none at all is a blind report rather
    than a clean one, and the two have to be told apart.
    """
    expected: list[str] = []
    extracted: set[str] = set()
    problems: list[Problem] = []
    total = 0

    for run in sarif.get("runs", []):
        for invocation in run.get("invocations", []):
            for notification in invocation.get("toolExecutionNotifications", []):
                total += 1
                rule_id = notification.get("descriptor", {}).get("id", "(unidentified)")
                if rule_id == _EXPECTED_ID + language:
                    expected.extend(_locations(notification))
                elif rule_id.endswith(_EXTRACTED_ID_SUFFIX):
                    extracted.update(_locations(notification))
                elif notification.get("level") in _PROBLEM_LEVELS:
                    problems.append(
                        Problem(
                            level=notification["level"],
                            rule_id=rule_id,
                            # One line: these are multi-line whenever the type-checker lists more
                            # than one error, and a newline ends the markdown table row below.
                            message=" ".join(
                                notification.get("message", {}).get("text", "").split()
                            ),
                        )
                    )

    return Coverage(tuple(expected), frozenset(extracted)), problems, total


def report(
    language: str, coverage: Coverage, problems: list[Problem], total: int
) -> tuple[list[str], list[str]]:
    """The step-summary markdown and the workflow commands to emit, for one language's SARIF."""
    summary = [f"## CodeQL extraction: {language}", ""]
    commands = []

    if total == 0:
        summary.append(
            "This SARIF carried **no diagnostics at all**, so neither file coverage nor "
            "extraction problems could be read. The CodeQL Action includes diagnostics based on a "
            "feature flag it resolves from GitHub, not on anything in this repo."
        )
        commands.append(
            f"::warning title=CodeQL {language} extraction unverified::"
            f"The {language} SARIF carried no diagnostics, so extraction coverage was not checked."
        )
        return summary, commands

    unextracted = coverage.unextracted(language)
    if coverage.has_baseline:
        covered = sum(1 for path in coverage.expected if path in coverage.extracted)
        summary += [
            f"**{covered} of {len(coverage.expected)}** expected `{language}` files extracted.",
            "",
        ]
    else:
        summary += [
            "This analysis published no expected-files baseline, so file coverage is not "
            "checked here.",
            "",
        ]

    if unextracted:
        summary += [
            "**This repo's own code went unanalysed.** These files were expected and not "
            "extracted, which is not upstream lag — it fails the job.",
            "",
            *(f"- `{path}`" for path in unextracted),
            "",
        ]
        commands.append(
            f"::error title=CodeQL {language} did not extract this repo's code::"
            f"{len(unextracted)} expected file(s) never reached the database, so the analysis "
            f"covered less than the tree: {', '.join(unextracted)}"
        )

    if problems:
        summary += [
            f"**{len(problems)} extraction problem(s).** The extractor gave up on code outside "
            "this repo — the pinned toolchain's own standard library, or a dependency — so this "
            "analysis read less than a passing check would imply.",
            "",
            f"**To clear this:** wait for a CodeQL bundle whose `{language}` extractor supports "
            "the toolchain this repo pins, or drop the toolchain bump that outran it. There is "
            "nothing to fix in this repo and nothing to override.",
            "",
            "| Level | Diagnostic | Message |",
            "| --- | --- | --- |",
            *(f"| {p.level} | `{p.rule_id}` | {p.message} |" for p in problems),
            "",
        ]
        commands.append(
            f"::error title=CodeQL {language} extraction is incomplete::"
            f"{len(problems)} extraction problem(s), so this analysis covered less than it "
            f"appears to. Wait for a CodeQL bundle whose {language} extractor supports the "
            "pinned toolchain, or drop the bump that outran it. See this job's summary."
        )

    if not unextracted and not problems:
        summary.append(
            "Clean — every expected file extracted, no extraction problems."
            if coverage.has_baseline
            else "No extraction problems. Nothing here speaks to file coverage."
        )

    return summary, commands


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sarif", type=Path, help="SARIF file written by codeql-action/analyze")
    parser.add_argument("--language", default="", help="CodeQL language name; keys the report")
    parser.add_argument(
        "--summary",
        type=Path,
        default=None,
        help="file to append the markdown report to; CI passes $GITHUB_STEP_SUMMARY",
    )
    args = parser.parse_args(argv)

    language = args.language or args.sarif.stem

    try:
        sarif = json.loads(args.sarif.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # Our own wiring, not the analysis: the path comes from the analyze step's `sarif-output`.
        print(f"::error title=CodeQL report could not read its SARIF::{args.sarif}: {exc}")
        return 1

    coverage, problems, total = read_sarif(sarif, language)
    summary, commands = report(language, coverage, problems, total)

    for line in summary:
        print(line)
    for command in commands:
        print(command)

    if args.summary is not None:
        with args.summary.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(summary) + "\n")

    return 1 if coverage.unextracted(language) or problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
