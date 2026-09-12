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

Anything `_ACCEPTED` cannot clear blocks, rather than only a list of known-bad types. An error type
this script has never seen is one it cannot classify, and an unevaluated check is not a pass. The
same argument applies one level down, which is why `_ACCEPTED` maps to a predicate and not a
string: two errors carrying one type can differ in whether the file was fully read, so an
unevaluated *instance* is not a pass either. The cost
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
#
# These are the strings semgrep *serialises*, which are not its variant names: `OutOfMemory.kind`
# is "OutOfMemory" while `OutOfMemory.to_json()` is "Out of memory", and the JSON carries the
# latter. Taking them from the `ErrorType` union instead puts six plausible strings here that never
# match — invisible, because an unmatched type blocks anyway. It just blocks without the reason.
_GAVE_UP_ON_A_TARGET = (
    "Timeout",
    "Out of memory",
    "Stack overflow",
    "Too many matches",
    "Fixpoint timeout",
    "Timeout during interfile analysis",
    "OOM during interfile analysis",
)


def _only_one_line_was_dropped(entry):
    """The reason a `PartialParsing` is harmless, or None when it is not.

    Acceptance is per **instance**, not per type, because the type says nothing about how much was
    lost. PEP 758's unparenthesized `except` drops the offending clause and nothing else — the real
    span is `83:21` to `83:32` — and rules still match before, after and inside the handler. A
    different cause on the same type swallows a region: an unclosed dict literal reported at line 3
    produced a span of lines 3-7 and lost a `subprocess(shell=True)` finding at line 7 that the same
    file without the typo reports. Both arrive as a bare `PartialParsing`.

    So the discriminator is the span, which is what actually decides whether coverage was lost: a
    span inside one line dropped a fragment, a span crossing lines swallowed code. Absent or
    malformed spans are not accepted — an instance this cannot measure is one it cannot clear.
    """
    spans = entry.get("spans")
    if not isinstance(spans, list) or not spans:
        return None
    for span in spans:
        if not isinstance(span, dict):
            return None
        start_line = (span.get("start") or {}).get("line")
        end_line = (span.get("end") or {}).get("line")
        if start_line is None or end_line is None or start_line != end_line:
            return None
    return (
        "unparsed region confined to one line — semgrep's Python grammar predates PEP 758, and "
        "measured, only the offending clause is dropped"
    )


# Error types that *can* leave coverage intact, mapped to the test deciding whether this particular
# instance did. A bare type is never enough: see `_only_one_line_was_dropped`.
_ACCEPTED = {
    "PartialParsing": _only_one_line_was_dropped,
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


def _accepted_reason(entry):
    """Why this error left coverage intact, or None if nothing here can say that it did."""
    decide = _ACCEPTED.get(error_type(entry))
    return decide(entry) if decide is not None else None


def blocking_errors(report):
    """Every error that leaves the scan's coverage in doubt, worst-understood first."""
    errors = report.get("errors")
    if not isinstance(errors, list):
        raise ValueError(
            "no `errors` list in the semgrep report; the output schema is not what this "
            "check reads, so it cannot tell whether a file was dropped"
        )
    return [e for e in errors if _accepted_reason(e) is None]


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

    accepted = [e for e in report.get("errors", []) if _accepted_reason(e) is not None]
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
