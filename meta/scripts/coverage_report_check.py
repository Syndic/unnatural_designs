#!/usr/bin/env python3
"""Fails when `bazel coverage` produced a report that measured less than the tree tests.

`bazel coverage //...` exits 0 whenever the tests pass, which is a different claim from "every
language's tests were measured". A ruleset that has no coverage tool for a language just warns
and leaves that language out of the merged lcov; rules_python did exactly this for any Python
version its wheel table did not know. So this reads the merged report and fails, as `Coverage`,
when a language that has test targets contributed nothing to it.

What the tree tests is asked of Bazel: `tests(//...)` minus `manual` tests, then minus any test
`cquery` finds incompatible with the platform `bazel coverage` built for, since those are skipped.
The flags the coverage run used are passed through after `--` so that `cquery` sees the same
platform. Each remaining test's rule kind is mapped to the source extension its coverage lands
under. That table is kept by hand, so an unknown kind fails rather than being skipped: a new
language would otherwise pass having measured nothing, which is the gap this check exists to close.
A kind that genuinely emits no coverage (a `diff_test`, say) goes in `_NO_COVERAGE_KINDS` with its
reason.

Whether coverage is *high enough* is not this check's question. Codecov's own statuses answer
that, and stay advisory; this one only asks whether coverage ran at all.

Usage: ./meta/scripts/coverage_report_check.py <lcov-file> [--summary <file>] [-- <bazel flags>]
"""

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from meta.scripts._workspace import exit_status, workspace_root

# Test rule kind -> the extension its sources' `SF:` records carry in the merged lcov.
_EXTENSIONS_BY_KIND = {
    "go_test": ".go",
    "py_test": ".py",
}

# Test rule kinds that run under `bazel coverage` but instrument nothing, one reason each.
_NO_COVERAGE_KINDS: dict[str, str] = {}

# Bazel's exact-tag idiom: `attr` matches a list attribute's string form, `[a, b]`, and `\b` would
# also match `manual-review`, dropping a test `bazel coverage` still runs.
MANUAL_TAG = r"[\[ ]manual[,\]]"

# What `bazel coverage //...` runs before platform filtering: every test minus the `manual` ones.
TESTS_QUERY = f'tests(//...) except attr(tags, "{MANUAL_TAG}", //...)'

# Printed by `cquery` per target: its label, or a blank line when the platform is incompatible.
COMPATIBLE_LABEL_EXPR = (
    '"" if "IncompatiblePlatformProvider" in providers(target) else str(target.label)'
)


@dataclass(frozen=True)
class Measured:
    """What the merged lcov holds for one source extension."""

    files: int
    lines_hit: int


def _canonical(label: str) -> str:
    """`cquery` prints main-repo labels as `@@//pkg:name`, `query` as `//pkg:name`."""
    return label.strip().lstrip("@")


def kinds_by_label(query_output: str) -> dict[str, str]:
    """Rule kind per label from `bazel query --output=label_kind` (`<kind> rule <label>`)."""
    kinds = {}
    for line in query_output.splitlines():
        if line.strip():
            fields = line.split()
            kinds[_canonical(fields[-1])] = fields[0]
    return kinds


def compatible_labels(cquery_output: str) -> set[str]:
    """Labels `COMPATIBLE_LABEL_EXPR` printed; incompatible targets printed blank lines."""
    return {_canonical(line) for line in cquery_output.splitlines() if line.strip()}


def expected_kinds(kinds: dict[str, str], compatible: set[str]) -> set[str]:
    """Rule kinds of the tests `bazel coverage` actually ran."""
    return {kind for label, kind in kinds.items() if label in compatible}


def _bazel(root: Path, args: list[str]) -> str:
    result = subprocess.run(["bazel", *args], cwd=root, capture_output=True, text=True, check=True)
    return result.stdout


def query_test_kinds(root: Path, bazel_flags: list[str]) -> set[str]:
    """Rule kinds come from `query`, which needs no configuration; compatibility needs one."""
    kinds = kinds_by_label(_bazel(root, ["query", TESTS_QUERY, "--output=label_kind"]))
    compatible = compatible_labels(
        _bazel(
            root,
            [
                "cquery",
                TESTS_QUERY,
                *bazel_flags,
                "--output=starlark",
                f"--starlark:expr={COMPATIBLE_LABEL_EXPR}",
            ],
        )
    )
    return expected_kinds(kinds, compatible)


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


def _emit(summary: list[str], commands: list[str], summary_path: Path | None) -> int:
    for line in summary + commands:
        print(line)
    if summary_path is not None:
        with summary_path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(summary) + "\n")
    return exit_status(len(commands))


def _failure(title: str, detail: str, summary_path: Path | None) -> int:
    """A check that could not run, reported through the same channels as one that failed."""
    return _emit(
        ["## Coverage collection", "", f"- {title}: {detail}"],
        [f"::error title={title}::{detail}"],
        summary_path,
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lcov", type=Path, help="merged lcov written by `bazel coverage`")
    parser.add_argument(
        "--summary",
        type=Path,
        default=None,
        help="file to append the markdown report to; CI passes $GITHUB_STEP_SUMMARY",
    )
    parser.add_argument(
        "bazel_flags",
        nargs="*",
        help="after `--`: the flags `bazel coverage` ran with, so `cquery` sees its platform",
    )
    args = parser.parse_args(argv)

    try:
        text = args.lcov.read_text(encoding="utf-8")
    except OSError as exc:
        return _failure("Coverage report could not be read", f"{args.lcov}: {exc}", args.summary)

    try:
        kinds = query_test_kinds(workspace_root(), args.bazel_flags)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        # The expected set is unknown, so a pass here would be a check that did not happen.
        return _failure("Coverage expectation could not be computed", str(exc), args.summary)

    summary, commands = report(kinds, read_lcov(text))
    return _emit(summary, commands, args.summary)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
