#!/usr/bin/env python3
"""Classify a PR/push's changed files into named boolean outputs for GitHub Actions.

Diff the changes a branch introduced, test each changed path against a set of named regexes,
and emit one `<name>=true|false` per group to `$GITHUB_OUTPUT` so later steps can gate on it.
The callers supply the rules; used by renovate-derived-files.yml and devcontainer.yml.

Two decisions live here so every caller inherits them:

  - **Three-dot diff.** `git diff <base>...HEAD` compares the merge base to HEAD, so a base
    that advanced under an open PR does not read as the branch's own changes. Two-dot would
    misclassify a PR the moment `main` picked up an unrelated change.
  - **Branch-creation short-circuit.** A branch-creating push has an all-zero base SHA that no
    diff can resolve; treat it as "everything changed" rather than silently skipping.

Pure functions carry the logic and the tests exercise them without git or the Actions
environment — the same split as ratify_renovate_proposals.py.

Usage:
  python3 meta/scripts/classify_changed_paths.py --base <ref-or-sha> \\
    --rule '<name>=<regex>' [--rule ...]
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

# git's null object: an all-zero object id, delivered as `github.event.before` on the push
# that first creates a branch. Any length of zeros counts (abbreviated or full 40/64 hex).
_NULL_OID_RE = re.compile(r"\A0+\Z")


# ── Pure functions (the part the tests exercise) ──────────────────────────────


def parse_rule(spec: str) -> tuple[str, str]:
    """Split a `name=regex` rule spec. Splits on the first `=` so the regex may contain `=`."""
    name, sep, pattern = spec.partition("=")
    if not sep or not name:
        raise ValueError(f"rule must be 'name=regex', got: {spec!r}")
    return name, pattern


def is_branch_creation(base: str) -> bool:
    """True if `base` is git's all-zero null oid (a branch-creating push has no real base)."""
    return bool(_NULL_OID_RE.match(base))


def classify(files: list[str], rules: dict[str, str]) -> dict[str, bool]:
    """Map each rule name to whether any changed file matches its regex (patterns self-anchor)."""
    return {name: any(re.search(pattern, f) for f in files) for name, pattern in rules.items()}


def format_outputs(result: dict[str, bool]) -> str:
    """Render the classification as GitHub Actions step-output lines (`name=true|false\\n`)."""
    return "".join(f"{name}={'true' if hit else 'false'}\n" for name, hit in result.items())


# ── I/O wrappers (thin shells around subprocess + file I/O) ───────────────────


def _git_changed_files(base: str) -> list[str]:
    """Return the files this branch changed vs `base`, using a three-dot (merge-base) diff."""
    out = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...HEAD", "--"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [line for line in out.splitlines() if line]


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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="Base ref or SHA to diff HEAD against.")
    parser.add_argument(
        "--rule",
        action="append",
        default=[],
        required=True,
        metavar="NAME=REGEX",
        help="A named path group; repeatable. Emits NAME=true if any changed path matches REGEX.",
    )
    args = parser.parse_args(argv)

    rules = dict(parse_rule(r) for r in args.rule)

    if is_branch_creation(args.base):
        print(f"Base {args.base} is the null oid (branch creation); all groups treated as changed.")
        result = {name: True for name in rules}
    else:
        files = _git_changed_files(args.base)
        print(f"Changed vs {args.base}:")
        print("".join(f"  {f}\n" for f in files) or "  (none)\n", end="")
        result = classify(files, rules)

    output = format_outputs(result)
    print("Classification:")
    print("".join(f"  {line}\n" for line in output.splitlines()), end="")
    _emit_github_output(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
