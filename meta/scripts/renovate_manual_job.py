#!/usr/bin/env python3
"""Tick the Mend "manual job" checkbox in a Renovate Dependency Dashboard issue body.

A PR that Renovate automerges produces no Renovate job, leaving every other open Renovate PR
behind the moved base until the next scheduled run; ticking this checkbox requests one. Why that
is the lever, and the evidence behind it, live in .claude/CLAUDE.md "Renovate run after automerge".

Reads the issue body on stdin, writes the ticked body to stdout, and reports which of three states
it found to `$GITHUB_OUTPUT` so the caller can skip a no-op API write. `absent` is reported rather
than raised: the marker is Mend's, not OSS Renovate's, so it can disappear without a Renovate
release, and the caller decides whether that is fatal.

The three status strings are a contract with renovate-run-after-automerge.yml, which gates steps on
their literal values; test_renovate_manual_job.py pins them.

Usage:
  python3 meta/scripts/renovate_manual_job.py < body.md > body.new.md
"""

from __future__ import annotations

import argparse
import os
import re
import sys

# The dashboard line reads:
#   - [ ] <!-- manual job -->Check this box to trigger a request for Renovate to run again ...
# Anchored on the HTML comment rather than the prose: the prose is user-visible text Mend may
# reword, while the marker is what its webhook handler keys on.
_MANUAL_JOB_RE = re.compile(
    r"^(?:[ \t]*[-*][ \t]+\[)(?P<mark>[ xX])(?:\][ \t]*<!--[ \t]*manual job[ \t]*-->)",
    re.MULTILINE,
)

# What `tick_manual_job` did — the contract renovate-run-after-automerge.yml gates steps on.
TICKED = "ticked"
ALREADY_TICKED = "already-ticked"
ABSENT = "absent"

# What the checkbox currently *is*, independent of any edit — what `--check` reports. Kept a
# separate vocabulary because "ticked" as an outcome ("I ticked it") and as a state ("it is
# ticked") are different claims, and the polling probe needs the second.
STATE_CLEAR = "clear"
STATE_TICKED = "ticked"
STATE_ABSENT = "absent"


# ── Pure functions (the part the tests exercise) ──────────────────────────────


def _state_from_match(match: re.Match[str] | None) -> str:
    """Map a `_MANUAL_JOB_RE` match (or its absence) to a state constant."""
    if match is None:
        return STATE_ABSENT
    return STATE_CLEAR if match["mark"] == " " else STATE_TICKED


def manual_job_state(body: str) -> str:
    """Report the checkbox's state: STATE_CLEAR, STATE_TICKED or STATE_ABSENT.

    Read by the scheduled probe, which ticks the box and then waits for Renovate to clear it. It
    shares `_state_from_match` with `tick_manual_job` so the probe's idea of the marker cannot
    drift from the transform's — a probe matching a shape the transform no longer writes would
    pass while the mechanism was broken.
    """
    return _state_from_match(_MANUAL_JOB_RE.search(body))


def tick_manual_job(body: str) -> tuple[str, str]:
    """Tick the manual-job checkbox, returning `(body, status)`.

    Status is one of TICKED, ALREADY_TICKED or ABSENT; the body comes back untouched for the
    latter two. Only the one character inside the brackets is rewritten, so sibling checkboxes
    (the per-branch `rebase-branch=` ones) and the rest of the body survive byte-for-byte.
    """
    match = _MANUAL_JOB_RE.search(body)
    if match is None:
        return body, ABSENT
    if _state_from_match(match) == STATE_TICKED:
        return body, ALREADY_TICKED
    return f"{body[: match.start('mark')]}x{body[match.end('mark') :]}", TICKED


# ── I/O wrappers (thin shells around file I/O) ────────────────────────────────


def _emit_github_output(text: str) -> None:
    """Append step outputs to $GITHUB_OUTPUT; print to stderr if unset (local invocation)."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        print(f"GITHUB_OUTPUT not set; would emit:\n{text}", file=sys.stderr, end="")
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(text)


# ── Driver ────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report the checkbox state (clear|ticked|absent) on stdout and exit, without "
        "emitting a body. Used by the scheduled probe to wait for Renovate to clear the box.",
    )
    args = parser.parse_args(argv)

    if args.check:
        print(manual_job_state(sys.stdin.read()))
        return 0

    body, status = tick_manual_job(sys.stdin.read())
    # Diagnostics on stderr: stdout carries the body.
    print(f"manual-job checkbox: {status}", file=sys.stderr)
    _emit_github_output(f"status={status}\n")
    sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
