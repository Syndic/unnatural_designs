#!/usr/bin/env python3
"""Reports what a CodeQL analysis actually read, and fails when it read less than the tree.

CodeQL concludes success whenever the *analysis* completed, which is a different claim from "the
extractor read the code". The gap is easy to miss: the runner does turn the extractor's
`##[error]` lines into check-run annotations, but they arrive untitled, at paths the runner
mis-parsed out of the log text, and under a green check — so they read as noise rather than as
"part of the analysis did not happen". This script reads the SARIF the CLI wrote, where the same
facts are recorded properly as `toolExecutionNotifications`, and reports them as one titled
annotation plus a step summary.

Two things fail the job, and they have different remedies:

  - **Extraction problems.** Error- and warning-level diagnostics: the extractor gave up on
    something. These carry no location at all (verified against a real run), so they cannot be
    attributed to a file, and today they are the toolchain's own standard library rather than
    ours. Nothing in this repo fixes that, but a partly-extracted analysis is indistinguishable
    from a clean one, so it cannot be allowed to pass — see .claude/CLAUDE.md "CodeQL runs as
    advanced setup" for what makes failing affordable.
  - **File coverage**, Go only. `<lang>/diagnostics/successfully-extracted-files` says what
    reached the database. What *should* have is computed here from the tree rather than taken
    from CodeQL's own baseline, which is skipped on pull requests since April 2026.

Coverage is Go-only because Go is the only language that builds. `build-mode: none` hands the
extractor every file matching the language, so a file cannot be silently left out; `autobuild`
runs `go build`, which submits only what it compiles. That difference is the entire reason this
half exists — the nine `_test.go` files in this repo are unextracted on every run and raise no
error at all, because they are never offered.

There is deliberately no path that reports "could not check". The two inputs are pinned by
environment variables on the job, so their absence means something is wrong rather than that a
default moved, and it fails like anything else.

Usage: ./meta/scripts/codeql_extraction_report.py <sarif-file> --language <name>
"""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from meta.scripts._workspace import find_files, registered_modules, workspace_root

_EXTRACTED_ID_SUFFIX = "/diagnostics/successfully-extracted-files"

# Levels that mean the extractor gave up on something, rather than reporting what it did.
_PROBLEM_LEVELS = ("error", "warning")

# The one language whose build decides what reaches the extractor; see the module docstring.
_BUILT_LANGUAGE = "go"


@dataclass(frozen=True)
class Problem:
    """One error- or warning-level `toolExecutionNotification`."""

    level: str
    rule_id: str
    message: str


def expected_files(root: Path, language: str) -> list[str]:
    """What the build should have submitted, read from the tree. Empty for unbuilt languages.

    `_test.go` is excluded because `go build` never compiles test files, so `autobuild` never
    offers them — they are outside the compile set by construction, not by concession. Widening
    this to quiet a red run is the move the coverage half exists to prevent.
    """
    if language != _BUILT_LANGUAGE:
        return []
    return sorted(
        path.relative_to(root).as_posix()
        for module in registered_modules(root)
        for path in find_files(root / module, "*.go")
        if not path.name.endswith("_test.go")
    )


def _locations(notification: dict) -> list[str]:
    return [
        uri
        for location in notification.get("locations", [])
        if (uri := location.get("physicalLocation", {}).get("artifactLocation", {}).get("uri"))
    ]


def read_sarif(sarif: dict) -> tuple[frozenset[str], list[Problem], int]:
    """Extracted files and extraction problems, plus the total notification count.

    That count is the non-vacuity signal: a SARIF with no notifications at all is a blind report
    rather than a clean one, and `CODEQL_ACTION_EXPORT_DIAGNOSTICS` is set on the job precisely so
    the two can be told apart.
    """
    extracted: set[str] = set()
    problems: list[Problem] = []
    total = 0

    for run in sarif.get("runs", []):
        for invocation in run.get("invocations", []):
            for notification in invocation.get("toolExecutionNotifications", []):
                total += 1
                rule_id = notification.get("descriptor", {}).get("id", "(unidentified)")
                if rule_id.endswith(_EXTRACTED_ID_SUFFIX):
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

    return frozenset(extracted), problems, total


def report(
    language: str,
    expected: list[str],
    extracted: frozenset[str],
    problems: list[Problem],
    total: int,
) -> tuple[list[str], list[str]]:
    """The step-summary markdown and the workflow commands to emit, for one language's SARIF."""
    summary = [f"## CodeQL extraction: {language}", ""]
    commands: list[str] = []

    if total == 0:
        summary.append(
            "This SARIF carried **no diagnostics at all**, so nothing about this analysis could "
            "be checked. `CODEQL_ACTION_EXPORT_DIAGNOSTICS` is set on this job to make that "
            "impossible, so its absence means the job's configuration is not doing what it says."
        )
        commands.append(
            f"::error title=CodeQL {language} extraction unverified::"
            f"The {language} SARIF carried no diagnostics, so extraction could not be checked at "
            "all. Check that CODEQL_ACTION_EXPORT_DIAGNOSTICS is still set on the codeql job."
        )
        return summary, commands

    unextracted = [path for path in expected if path not in extracted]

    if language == _BUILT_LANGUAGE:
        if not expected:
            summary.append(
                "**No Go source found in the tree.** The coverage check has nothing to compare "
                "against, which is not a pass — `go.work` should register at least one module."
            )
            commands.append(
                f"::error title=CodeQL {language} coverage could not be checked::"
                "No non-test .go files were found across go.work's modules, so file coverage was "
                "not verified. Check go.work's use directives."
            )
            return summary, commands
        summary += [
            f"**{len(expected) - len(unextracted)} of {len(expected)}** non-test `.go` files in "
            "the tree reached the database.",
            "",
        ]
    else:
        summary += [
            f"{len(extracted)} file(s) extracted. Coverage is not gated for `{language}`: "
            "`build-mode: none` offers the extractor every matching file, so none can be left "
            "out by the build.",
            "",
        ]

    if unextracted:
        summary += [
            "**This repo's own code went unanalysed.** These files are in the tree and never "
            "reached the database, which is not upstream lag — it fails the job.",
            "",
            *(f"- `{path}`" for path in unextracted),
            "",
        ]
        commands.append(
            f"::error title=CodeQL {language} did not extract this repo's code::"
            f"{len(unextracted)} file(s) in the tree never reached the database, so the analysis "
            f"covered less than the source: {', '.join(unextracted)}"
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
        summary.append("Clean — everything the build offers reached the database.")

    return summary, commands


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sarif", type=Path, help="SARIF file written by codeql-action/analyze")
    parser.add_argument("--language", default="", help="CodeQL language name; keys the report")
    parser.add_argument(
        "--source-root",
        type=Path,
        default=None,
        help="tree to read the expected file set from (default: the git workspace root)",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=None,
        help="file to append the markdown report to; CI passes $GITHUB_STEP_SUMMARY",
    )
    args = parser.parse_args(argv)

    language = args.language or args.sarif.stem
    root = args.source_root if args.source_root is not None else workspace_root()

    try:
        sarif = json.loads(args.sarif.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # Our own wiring, not the analysis: the path comes from the analyze step's `sarif-output`.
        print(f"::error title=CodeQL report could not read its SARIF::{args.sarif}: {exc}")
        return 1

    extracted, problems, total = read_sarif(sarif)
    expected = expected_files(root, language)
    summary, commands = report(language, expected, extracted, problems, total)

    for line in summary:
        print(line)
    for command in commands:
        print(command)

    if args.summary is not None:
        with args.summary.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(summary) + "\n")

    return 1 if commands else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
