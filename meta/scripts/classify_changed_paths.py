#!/usr/bin/env python3
"""Classify a PR/push's changed files into named boolean outputs for GitHub Actions.

Diff the changes a branch introduced, test each changed path against a named pattern set, and emit
one `<name>=true|false` per set to `$GITHUB_OUTPUT` so later steps can gate on it. The sets live in
`path_classification_pattern_sets.py`; callers name the ones they want. Used by
renovate-derived-files.yml and devcontainer.yml.

Three decisions live here so every caller inherits them:

  - **Three-dot diff.** `git diff <base>...HEAD` compares the merge base to HEAD, so a base
    that advanced under an open PR does not read as the branch's own changes. Two-dot would
    misclassify a PR the moment `main` picked up an unrelated change.
  - **Branch-creation short-circuit.** A branch-creating push has an all-zero base SHA that no
    diff can resolve; treat it as "everything changed" rather than silently skipping.
  - **The sets are shared, not passed in.** They used to arrive as `--rule name=regex`
    arguments, which meant each caller wrote its own copy and the sets that had to agree agreed
    only by review. The module is now the single definition and callers select from it.

Pure functions carry the logic and the tests exercise them without git or the Actions
environment — the same split as ratify_renovate_proposals.py.

Usage:
  python3 meta/scripts/classify_changed_paths.py --base <ref-or-sha> \\
    --emit <name> [--emit <name> ...]
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

# Run as a script rather than through `bazel run`, the workspace root is not on sys.path, so
# `from meta.scripts.X` would fail. Adding it explicitly fixes that and is harmless under bazel
# py_binary, where rules_python already makes the import resolvable.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from meta.scripts.path_classification_pattern_sets import SETS

# git's null object: an all-zero object id, delivered as `github.event.before` on the push
# that first creates a branch. Any length of zeros counts (abbreviated or full 40/64 hex).
_NULL_OID_RE = re.compile(r"\A0+\Z")


# ── Pure functions (the part the tests exercise) ──────────────────────────────


def is_branch_creation(base: str) -> bool:
    """True if `base` is git's all-zero null oid (a branch-creating push has no real base)."""
    return bool(_NULL_OID_RE.match(base))


def classify(files: list[str], sets: dict[str, tuple[str, ...]]) -> dict[str, bool]:
    """Map each set name to whether any changed file matches one of its patterns."""
    return {
        name: any(re.search(pattern, f) for pattern in patterns for f in files)
        for name, patterns in sets.items()
    }


def select(names: list[str]) -> dict[str, tuple[str, ...]]:
    """The named sets, in the order asked for. An unknown name is an error, not an empty set:
    a caller naming a set that does not exist would otherwise emit `name=false` on every run and
    gate its steps off forever."""
    missing = sorted(n for n in names if n not in SETS)
    if missing:
        raise SystemExit(f"no such pattern set(s): {', '.join(missing)}")
    return {name: SETS[name] for name in names}


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
        "--emit",
        action="append",
        default=[],
        required=True,
        metavar="NAME",
        help="A pattern set to classify into; repeatable. Emits NAME=true if any changed path "
        "matches it.",
    )
    args = parser.parse_args(argv)

    sets = select(args.emit)

    if is_branch_creation(args.base):
        print(f"Base {args.base} is the null oid (branch creation); all sets treated as changed.")
        result = dict.fromkeys(sets, True)
    else:
        files = _git_changed_files(args.base)
        print(f"Changed vs {args.base}:")
        print("".join(f"  {f}\n" for f in files) or "  (none)\n", end="")
        result = classify(files, sets)

    output = format_outputs(result)
    print("Classification:")
    print("".join(f"  {line}\n" for line in output.splitlines()), end="")
    _emit_github_output(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
