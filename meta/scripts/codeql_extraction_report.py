#!/usr/bin/env python3
"""Turns CodeQL's extraction diagnostics into an annotation, a step summary, and an exit code.

CodeQL reports success whenever the *analysis* completed, even when the extractor gave up on part
of the code it was asked to read. The gap it leaves is invisible: GitHub raises no check-run
annotation for it, so the only record is `##[error]` lines inside a green job's raw log. This
script reads the SARIF the CLI just wrote — where those same failures are recorded as
`toolExecutionNotifications` — and re-emits them where a human or the annotations API will see
them.

What it gates on is *whose* code failed to extract, which is the axis that decides whether anyone
here can act:

  - **Inside the source root** is our own code going unanalysed. Always ours to fix, never
    dependent on an upstream release, so it fails the job.
  - **Outside it** is the toolchain or a dependency the extractor could not parse. Nothing in
    this repo can fix that and the upstream fix ships on GitHub's schedule, so it warns loudly
    and stays green. What is degraded today, and why, is in .claude/CLAUDE.md "CodeQL runs as
    advanced setup".

There is deliberately no tolerance count to tune in either direction: the first is zero by
construction, and the second never fails, so neither can be quietly widened to make a red run
green.

Usage: ./meta/scripts/codeql_extraction_report.py <sarif-file> [--language <name>]
"""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

# SARIF notification levels worth reporting. "note" is the level the informational diagnostics use
# (`<lang>/diagnostics/successfully-extracted-files`), which is signal only in aggregate: it is how
# this script knows the diagnostic machinery ran at all.
_REPORTED_LEVELS = ("error", "warning")


@dataclass(frozen=True)
class Diagnostic:
    """One `toolExecutionNotification`, reduced to the fields this report prints."""

    level: str
    rule_id: str
    locations: tuple[str, ...]
    message: str

    @property
    def first_party(self) -> bool:
        """True when any location is in the analysed source root.

        CodeQL writes source-root files as repo-relative URIs and everything else — the Go
        toolchain's own stdlib, module cache entries — as absolute ones. A notification with no
        location at all is not claimed as ours: it is reported, but it does not fail the job,
        since there is nothing to point a reader at.
        """
        return any(not (uri.startswith("/") or "://" in uri) for uri in self.locations)

    @property
    def where(self) -> str:
        return ", ".join(self.locations) if self.locations else "(no location)"


def _notification_locations(notification: dict) -> tuple[str, ...]:
    uris = []
    for location in notification.get("locations", []):
        uri = location.get("physicalLocation", {}).get("artifactLocation", {}).get("uri")
        if uri:
            uris.append(uri)
    return tuple(uris)


def read_diagnostics(sarif: dict) -> tuple[list[Diagnostic], int]:
    """Every reported-level diagnostic in the SARIF, and the total notification count.

    The count is the non-vacuity signal. `codeql database interpret-results` only writes
    notifications when it is asked for them, and the CodeQL Action asks based on a server-side
    feature flag rather than anything in this repo — so "no diagnostics" is ambiguous between a
    clean extraction and a blind report, and the two have to be told apart.
    """
    diagnostics, total = [], 0
    for run in sarif.get("runs", []):
        for invocation in run.get("invocations", []):
            for notification in invocation.get("toolExecutionNotifications", []):
                total += 1
                level = notification.get("level", "none")
                if level not in _REPORTED_LEVELS:
                    continue
                diagnostics.append(
                    Diagnostic(
                        level=level,
                        rule_id=notification.get("descriptor", {}).get("id", "(unidentified)"),
                        locations=_notification_locations(notification),
                        message=notification.get("message", {}).get("text", "").strip(),
                    )
                )
    return diagnostics, total


def _table(diagnostics: list[Diagnostic]) -> list[str]:
    lines = ["| Level | Diagnostic | Location | Message |", "| --- | --- | --- | --- |"]
    for d in diagnostics:
        # One line per row: a newline inside a cell ends the table, and these messages are
        # multi-line whenever the type-checker lists more than one error for a package.
        message = " ".join(d.message.split())
        lines.append(f"| {d.level} | `{d.rule_id}` | `{d.where}` | {message} |")
    return lines


def report(language: str, diagnostics: list[Diagnostic], total: int) -> tuple[list[str], list[str]]:
    """The step-summary markdown and the workflow commands to emit, for one language's SARIF."""
    ours = [d for d in diagnostics if d.first_party]
    theirs = [d for d in diagnostics if not d.first_party]

    summary = [f"## CodeQL extraction: {language}", ""]
    commands = []

    if total == 0:
        summary += [
            "CodeQL wrote **no extraction diagnostics at all**, so this report cannot tell a "
            "clean extraction from a blind one. The CodeQL Action includes diagnostics in SARIF "
            "based on a feature flag it reads from GitHub, not on anything in this repo.",
        ]
        commands.append(
            f"::warning title=CodeQL {language} extraction unverified::"
            f"No diagnostics in the {language} SARIF, so extraction coverage could not be checked."
        )
        return summary, commands

    if not diagnostics:
        summary += [f"Clean — {total} diagnostics, none at error or warning level."]
        return summary, commands

    if theirs:
        packages = ", ".join(sorted({d.where for d in theirs}))
        summary += [
            f"**{len(theirs)} outside the source root.** The extractor could not read code this "
            "repo does not own — the pinned Go toolchain's standard library, or a dependency. "
            "Nothing here fixes that; it clears when GitHub ships a CodeQL bundle whose extractor "
            "has caught up.",
            "",
            *_table(theirs),
            "",
        ]
        commands.append(
            f"::warning title=CodeQL {language} extraction degraded::"
            f"{len(theirs)} location(s) outside the source root failed to extract, so this "
            f"analysis covered less than it appears to: {packages}"
        )

    if ours:
        summary += [
            f"**{len(ours)} inside the source root.** This repo's own code went unanalysed. "
            "Unlike the entries above this is not upstream lag, and it fails the job.",
            "",
            *_table(ours),
            "",
        ]
        commands.append(
            f"::error title=CodeQL {language} did not extract this repo's code::"
            f"{len(ours)} source-root location(s) failed to extract; CodeQL analysed less than "
            "the whole tree. Fix the extraction failure or the analysis is not covering the code "
            "it claims to."
        )

    return summary, commands


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sarif", type=Path, help="SARIF file written by codeql-action/analyze")
    parser.add_argument("--language", default="", help="CodeQL language name, for the report title")
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

    diagnostics, total = read_diagnostics(sarif)
    summary, commands = report(language, diagnostics, total)

    for line in summary:
        print(line)
    for command in commands:
        print(command)

    if args.summary is not None:
        with args.summary.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(summary) + "\n")

    return 1 if any(d.first_party for d in diagnostics) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
