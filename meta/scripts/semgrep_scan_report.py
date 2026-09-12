#!/usr/bin/env python3
"""Turns "the Semgrep job passed" back into a claim about what it actually scanned.

Semgrep's per-rule budget is not a rule-level failure: when a rule exceeds it, semgrep drops **the
whole file**, records the reason in `errors[]`, prints `CI scan completed successfully`, and exits
0. So a file leaving the scan is indistinguishable from a file scanned clean, and is visible only
to someone reading a green job's log. That is #294, where `MODULE.bazel.lock` sat outside the
secret scan for months.

Raising the budget fixed that instance. This fixes the silence, which is the part that recurs: a
new rule pack, a larger generated file, or a slower runner puts some other file in the same place.

## Why it reads `errors[]` and not coverage

The obvious shape — port the file-coverage half of `codeql_extraction_report.py` — does not work
here, and fails in the direction that matters. Semgrep's `paths` object carries only `scanned`,
and a file dropped for a timeout is **still listed in it**. A coverage gate would therefore report
green on the exact bug this exists to catch. (`skipped_paths` would answer it, but it is part of
the app-upload payload and needs `SEMGREP_APP_TOKEN`, which this repo does not use.)

## Why unknown types fail

Anything not in `_ACCEPTED` blocks, rather than only a list of known-bad types. An error type this
script has never seen is one it cannot classify, and an unevaluated check is not a pass. The cost
is that a semgrep release adding a benign error type turns the job red once; the fix is one entry
here, with a reason, which is a decision someone makes rather than a default they inherit.

## Why it shares no code with the rest of meta/scripts

It runs inside the `semgrep/semgrep` container, which is Python 3.12. This tree is 3.14, and
`_workspace.py` is a `SyntaxError` under 3.12 — it uses PEP 758's unparenthesized `except`. So no
import from here can be used, including `_workspace.exit_status()`, and this file has to stay
within what 3.12 can parse.
"""

import argparse
import json
import sys

# Types semgrep reports when it could not finish looking at something. Not a closed list of what
# blocks — see the module docstring — but the ones worth naming because they are reachable here.
_GAVE_UP_ON_A_TARGET = (
    "Timeout",
    "OutOfMemory",
    "StackOverflow",
    "TooManyMatches_",
    "FixpointTimeout",
    "TimeoutDuringInterfile",
    "OutOfMemoryDuringInterfile",
)

# Error types that do **not** mean reduced coverage, each with the reason it is tolerated. Adding
# an entry is a deliberate call: it says "a file carrying this was still fully scanned".
_ACCEPTED = {
    "PartialParsing": (
        "semgrep's Python grammar predates PEP 758, so an unparenthesized multi-class `except` "
        "is unparsed. Measured: only that clause is dropped — rules still match before, after "
        "and inside the handler — so coverage is intact."
    ),
}


def error_type(entry):
    """The error's type tag, as a plain string.

    `type` is a variant, not a string: `Timeout` arrives bare, while `PartialParsing` arrives as
    `["PartialParsing", [spans]]`. Set membership or `.startswith()` over the raw value raises on
    the list form, which is how this reads as "semgrep exposes no type" to a caller that assumes
    the simpler shape.
    """
    raw = entry.get("type")
    if isinstance(raw, list):
        return str(raw[0]) if raw else ""
    return str(raw) if raw is not None else ""


def blocking_errors(report):
    """Every error that leaves the scan's coverage in doubt, worst-understood first."""
    errors = report.get("errors")
    if not isinstance(errors, list):
        raise ValueError(
            "no `errors` list in the semgrep report; the output schema is not what this "
            "check reads, so it cannot tell whether a file was dropped"
        )
    return [e for e in errors if error_type(e) not in _ACCEPTED]


def _describe(entry):
    kind = error_type(entry) or "<no type>"
    path = entry.get("path") or "<no path>"
    note = " — semgrep gave up on this target" if kind in _GAVE_UP_ON_A_TARGET else ""
    return f"{kind}: {path}{note}"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("report", help="the file written by `semgrep ci --json-output=`")
    parser.add_argument("--summary", help="append a GitHub step summary here")
    args = parser.parse_args(argv)

    try:
        with open(args.report, encoding="utf-8") as handle:
            report = json.load(handle)
    except (OSError, ValueError) as exc:
        # A report that cannot be read is not a pass: it is the same "could not check" state the
        # scan itself would have hidden.
        print(f"error: could not read the semgrep report: {exc}", file=sys.stderr)
        return 1

    try:
        blocking = blocking_errors(report)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    accepted = [e for e in report.get("errors", []) if error_type(e) in _ACCEPTED]
    lines = []
    if blocking:
        lines.append("## Semgrep scanned less than it reported")
        lines.append("")
        for entry in blocking:
            lines.append(f"- `{_describe(entry)}`")
        lines.append("")
        lines.append(
            "The job's own summary says the scan completed. These entries say part of the tree "
            "was not read, which the exit code does not carry."
        )
    else:
        lines.append("## Semgrep coverage intact")
        lines.append("")
        lines.append("No error left a target unscanned.")
    if accepted:
        lines.append("")
        lines.append("Tolerated, with coverage unaffected:")
        for entry in accepted:
            lines.append("- `{}` in `{}`".format(error_type(entry), entry.get("path")))

    text = "\n".join(lines)
    print(text)
    if args.summary:
        try:
            with open(args.summary, "a", encoding="utf-8") as handle:
                handle.write(text + "\n")
        except OSError as exc:
            print(f"warning: could not write the step summary: {exc}", file=sys.stderr)

    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
