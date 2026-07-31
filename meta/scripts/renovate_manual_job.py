#!/usr/bin/env python3
"""Tick the Mend "manual job" checkbox in a Renovate Dependency Dashboard issue body.

Mend-hosted Renovate enqueues a job from a webhook, but its webhook layer ignores events whose
sender is `renovate[bot]` — ordinary loop prevention, since Renovate pushes to its own branches
constantly. A PR that Renovate automerges is therefore invisible to it: nothing queues a run, and
every other open Renovate PR sits behind the moved base until the next scheduled one. The
Dependency Dashboard carries a Mend-only checkbox that requests a run when ticked, and ticking it
from a *different* sender is the lever out. Full rationale, including the measurements behind it,
in .claude/CLAUDE.md "Renovate run after automerge".

Reads the issue body on stdin, writes the ticked body to stdout, and reports which of three
states it found to `$GITHUB_OUTPUT` so the caller can skip a no-op API write. `absent` is
reported rather than raised: the marker is Mend's, not OSS Renovate's, so it can disappear
without a Renovate release, and the caller decides whether that is fatal.

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
    r"^(?P<lead>[ \t]*[-*][ \t]+\[)(?P<mark>[ xX])(?P<tail>\][ \t]*<!--[ \t]*manual job[ \t]*-->)",
    re.MULTILINE,
)

TICKED = "ticked"
ALREADY_TICKED = "already-ticked"
ABSENT = "absent"


# ── Pure functions (the part the tests exercise) ──────────────────────────────


def tick_manual_job(body: str) -> tuple[str, str]:
    """Tick the manual-job checkbox, returning `(body, status)`.

    Status is one of TICKED, ALREADY_TICKED or ABSENT; the body comes back untouched for the
    latter two. Only the one character inside the brackets is rewritten, so sibling checkboxes
    (the per-branch `rebase-branch=` ones) and the rest of the body survive byte-for-byte.
    """
    match = _MANUAL_JOB_RE.search(body)
    if match is None:
        return body, ABSENT
    if match["mark"] != " ":
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
    parser.parse_args(argv)

    body, status = tick_manual_job(sys.stdin.read())
    # Diagnostics on stderr: stdout carries the body.
    print(f"manual-job checkbox: {status}", file=sys.stderr)
    _emit_github_output(f"status={status}\n")
    sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
