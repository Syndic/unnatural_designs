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
    reached the database. What *should* have is asked of `go list` rather than taken from
    CodeQL's own baseline, which is skipped on pull requests since April 2026.

Coverage is Go-only because Go is the only language that builds. `build-mode: none` hands the
extractor every file matching the language, so a file cannot be silently left out; `autobuild`
runs `go build`, which submits only what it compiles. That difference is the entire reason this
half exists — the nine `_test.go` files in this repo are unextracted on every run and raise no
error at all, because they are never offered. Errors alone therefore cannot answer "was all of
it read".

There is deliberately no path that reports "could not check". The two inputs are pinned by
environment variables on the job, so their absence means something is wrong rather than that a
default moved, and it fails like anything else.

Usage: ./meta/scripts/codeql_extraction_report.py <sarif-file> --language <name>
"""

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from meta.scripts._workspace import registered_modules, workspace_root

_EXTRACTED_ID_SUFFIX = "/diagnostics/successfully-extracted-files"

# Extraction diagnostics specifically -- `go/diagnostics/extraction-errors` and its siblings.
# The toolchain-lag remedy below only makes sense for these; every other diagnostic the bundle
# emits (a deprecated flag, an unresolved import, unparseable YAML) still fails, but is reported
# as itself rather than handed a remedy that cannot be acted on.
_EXTRACTION_ID_MARKER = "/diagnostics/extraction-"

# Levels that mean the extractor gave up on something, rather than reporting what it did.
_PROBLEM_LEVELS = ("error", "warning")

# What SARIF means by an omitted `level` on a notification. Not a default of ours to choose.
_SARIF_DEFAULT_LEVEL = "warning"

# The one language whose build decides what reaches the extractor; see the module docstring.
_BUILT_LANGUAGE = "go"


@dataclass(frozen=True)
class Problem:
    """One error- or warning-level `toolExecutionNotification`."""

    level: str
    rule_id: str
    message: str

    @property
    def is_extraction(self) -> bool:
        """True when this is the extractor giving up, rather than some other diagnostic."""
        return _EXTRACTION_ID_MARKER in self.rule_id


def expected_files(root: Path, language: str) -> list[str]:
    """What the build submits to the extractor. Empty for languages that are not built.

    Asks `go list` rather than modelling the answer. `autobuild` runs `go build ./...`, and the
    set that compiles is not "every non-test .go file": the go tool also skips `testdata/` trees,
    `_`- and `.`-prefixed directories, and files excluded by build constraints
    (`foo_windows.go`, `//go:build` tags). A hand-rolled filter would have to grow an exclusion
    per convention, and each one would be indistinguishable from widening the gate to quiet a red
    run -- the move this check exists to prevent. `.GoFiles` is the same list the compiler gets,
    so there is nothing left to model.

    Cgo sources are deliberately absent: `.CgoFiles` would belong here, but the repo-wide no-cgo
    policy (//meta/scripts:check_no_cgo) means including them is structure for a case that cannot
    occur.
    """
    if language != _BUILT_LANGUAGE:
        return []

    files: set[str] = set()
    for module in registered_modules(root):
        module_dir = root / module
        result = subprocess.run(
            ["go", "list", "-f", "{{range .GoFiles}}{{$.Dir}}/{{.}}\n{{end}}", "./..."],
            cwd=module_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        for line in result.stdout.splitlines():
            if line.strip():
                files.add(Path(line.strip()).resolve().relative_to(root.resolve()).as_posix())
    return sorted(files)


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
                # SARIF defaults an absent `level` to "warning", so reading it as None would
                # drop a real extraction failure on the floor -- fail-open in the one gate that
                # must not be.
                elif notification.get("level", _SARIF_DEFAULT_LEVEL) in _PROBLEM_LEVELS:
                    problems.append(
                        Problem(
                            level=notification.get("level", _SARIF_DEFAULT_LEVEL),
                            rule_id=rule_id,
                            # One line: these are multi-line whenever the type-checker lists more
                            # than one error, and a newline ends the markdown table row below.
                            message=" ".join(
                                notification.get("message", {}).get("text", "").split()
                            ),
                        )
                    )

    return frozenset(extracted), problems, total


def _table(problems: list[Problem]) -> list[str]:
    return [
        "| Level | Diagnostic | Message |",
        "| --- | --- | --- |",
        *(f"| {p.level} | `{p.rule_id}` | {p.message} |" for p in problems),
    ]


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
            # Appended rather than returned: the extractor's own diagnostics are the thing this
            # script exists to surface, and a broken go.work must not bury them in the raw log.
            summary += [
                "**No Go source found in the tree.** The coverage check has nothing to compare "
                "against, which is not a pass — `go.work` should register at least one module.",
                "",
            ]
            commands.append(
                f"::error title=CodeQL {language} coverage could not be checked::"
                "`go list` reported no compiled files across go.work's modules, so file coverage "
                "was not verified. Check go.work's use directives."
            )
        else:
            summary += [
                f"**{len(expected) - len(unextracted)} of {len(expected)}** files the Go build "
                "compiles reached the database.",
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

    extraction = [p for p in problems if p.is_extraction]
    other = [p for p in problems if not p.is_extraction]

    if extraction:
        summary += [
            f"**{len(extraction)} extraction problem(s).** The extractor gave up on code outside "
            "this repo — the pinned toolchain's own standard library, or a dependency — so this "
            "analysis read less than a passing check would imply.",
            "",
            f"**To clear this:** wait for a CodeQL bundle whose `{language}` extractor supports "
            "the toolchain this repo pins, or drop the toolchain bump that outran it. There is "
            "nothing to fix in this repo and nothing to override.",
            "",
            *_table(extraction),
            "",
        ]
        commands.append(
            f"::error title=CodeQL {language} extraction is incomplete::"
            f"{len(extraction)} extraction problem(s), so this analysis covered less than it "
            f"appears to. Wait for a CodeQL bundle whose {language} extractor supports the "
            "pinned toolchain, or drop the bump that outran it. See this job's summary."
        )

    if other:
        # No remedy named on purpose: these are whatever else the bundle chose to report, and
        # inventing an action for them is how a gate starts telling people to do the wrong thing.
        summary += [
            f"**{len(other)} other diagnostic(s)** at error or warning level. Not extraction "
            "failures, so the remedy above does not apply — read the message and judge.",
            "",
            *_table(other),
            "",
        ]
        commands.append(
            f"::error title=CodeQL {language} reported a diagnostic::"
            f"{len(other)} diagnostic(s) at error or warning level that are not extraction "
            "failures. See this job's summary for what the bundle reported."
        )

    if not commands:
        # Keyed on the commands rather than on the individual failure lists: every branch above
        # that appends one is a reason this is not clean, and a second enumeration here would be
        # one edit away from claiming "Clean" alongside an error.
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
