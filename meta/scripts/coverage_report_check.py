#!/usr/bin/env python3
"""Fails when `bazel coverage` produced a report that measured less than the tree tests.

`bazel coverage //...` exits 0 whenever the tests pass, which is a different claim from "every
language's tests were measured". A ruleset that has no coverage tool for a language just warns
and leaves that language out of the merged lcov; rules_python did exactly this for any Python
version its wheel table did not know. So this reads the merged report and fails, as `Coverage`,
when a language that has test targets contributed nothing to it.

What the tree tests is asked of Bazel (`tests(//...)`, the same set `bazel coverage //...` runs),
and each test rule kind is mapped to the source extension its coverage lands under. That table is
kept by hand, so an unknown kind fails rather than being skipped: a new language would otherwise
pass having measured nothing, which is the gap this check exists to close. A kind that genuinely
emits no coverage (a `diff_test`, say) goes in `_NO_COVERAGE_KINDS` with its reason.

Whether coverage is *high enough* is not this check's question. Codecov's own statuses answer
that, and stay advisory; this one only asks whether coverage ran at all.

Usage: ./meta/scripts/coverage_report_check.py <lcov-file> [--summary <file>]
"""

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from meta.scripts._workspace import workspace_root

# Test rule kind -> the extension its sources' `SF:` records carry in the merged lcov.
_EXTENSIONS_BY_KIND = {
    "go_test": ".go",
    "py_test": ".py",
}

# Test rule kinds that run under `bazel coverage` but instrument nothing, one reason each.
_NO_COVERAGE_KINDS: dict[str, str] = {}

# What `bazel coverage //...` runs: every test under the root, minus the `manual` ones it skips.
_TESTS_QUERY = 'tests(//...) except attr(tags, "\\bmanual\\b", //...)'


@dataclass(frozen=True)
class Measured:
    """What the merged lcov holds for one source extension."""

    files: int
    lines_hit: int


def test_kinds(query_output: str) -> set[str]:
    """Rule kinds from `bazel query --output=label_kind`, whose lines read `<kind> rule <label>`."""
    return {line.split()[0] for line in query_output.splitlines() if line.strip()}


def query_test_kinds(root: Path) -> set[str]:
    result = subprocess.run(
        ["bazel", "query", _TESTS_QUERY, "--output=label_kind"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return test_kinds(result.stdout)


def read_lcov(text: str) -> dict[str, Measured]:
    """Files and hit lines per source extension.

    Hit lines rather than files alone, because a tracer that attached but recorded nothing still
    writes `SF:` records, and a language whose every line reads zero has not been measured.
    """
    files: dict[str, int] = {}
    hits: dict[str, int] = {}
    extension = None
    for line in text.splitlines():
        if line.startswith("SF:"):
            extension = Path(line[3:].strip()).suffix
            files[extension] = files.get(extension, 0) + 1
            hits.setdefault(extension, 0)
        elif line.startswith("DA:") and extension is not None:
            fields = line[3:].split(",")
            if len(fields) >= 2 and fields[1].strip().isdigit() and int(fields[1]) > 0:
                hits[extension] += 1
        elif line.startswith("end_of_record"):
            extension = None
    return {ext: Measured(files[ext], hits[ext]) for ext in files}


def findings(kinds: set[str], measured: dict[str, Measured]) -> list[str]:
    """Every reason the report does not show coverage ran for what the tree tests."""
    problems = []
    if not kinds:
        problems.append(
            "Bazel reported no test targets, so there is nothing to say coverage measured"
        )
    if not measured:
        problems.append("The merged lcov holds no `SF:` records at all")

    for kind in sorted(kinds - _EXTENSIONS_BY_KIND.keys() - _NO_COVERAGE_KINDS.keys()):
        problems.append(
            f"Test rule kind `{kind}` is not in coverage_report_check.py's table. Map it to the "
            "extension its coverage lands under, or record it in `_NO_COVERAGE_KINDS` with the "
            "reason it emits none"
        )

    for kind in sorted(kinds & _EXTENSIONS_BY_KIND.keys()):
        extension = _EXTENSIONS_BY_KIND[kind]
        found = measured.get(extension)
        if found is None:
            problems.append(
                f"`{kind}` targets exist, but the merged lcov has no `{extension}` files. That "
                "language's coverage tool did not run; look for a warning in the `bazel coverage` "
                "log"
            )
        elif found.lines_hit == 0:
            problems.append(
                f"The merged lcov lists {found.files} `{extension}` files but no line in any of "
                f"them was hit. `{kind}`'s coverage tool ran and recorded nothing"
            )
    return problems


def report(kinds: set[str], measured: dict[str, Measured]) -> tuple[list[str], list[str]]:
    """The step-summary markdown and the workflow commands to emit."""
    summary = [
        "## Coverage collection",
        "",
        "| Extension | Files | Lines hit |",
        "| --- | --- | --- |",
    ]
    summary += [f"| `{ext}` | {m.files} | {m.lines_hit} |" for ext, m in sorted(measured.items())]
    summary.append("")
    summary.append(f"Test rule kinds: {', '.join(f'`{k}`' for k in sorted(kinds)) or 'none'}")
    problems = findings(kinds, measured)
    commands = [f"::error title=Coverage did not measure the tree::{p}" for p in problems]
    summary.append("")
    summary += [f"- {p}" for p in problems] or ["Every language with test targets was measured."]
    return summary, commands


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lcov", type=Path, help="merged lcov written by `bazel coverage`")
    parser.add_argument(
        "--summary",
        type=Path,
        default=None,
        help="file to append the markdown report to; CI passes $GITHUB_STEP_SUMMARY",
    )
    args = parser.parse_args(argv)

    try:
        text = args.lcov.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"::error title=Coverage report could not be read::{args.lcov}: {exc}")
        return 1

    try:
        kinds = query_test_kinds(workspace_root())
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        # The expected set is unknown, so a pass here would be a check that did not happen.
        print(f"::error title=Coverage expectation could not be computed::`bazel query`: {exc}")
        return 1

    summary, commands = report(kinds, read_lcov(text))
    for line in summary + commands:
        print(line)
    if args.summary is not None:
        with args.summary.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(summary) + "\n")
    return 1 if commands else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
