#!/usr/bin/env python3
"""Classify a PR/push's changed files into named boolean outputs for GitHub Actions.

Two workflows need the same primitive: diff the changes a branch introduced, test each
changed path against a set of named regexes, and emit one `<name>=true|false` per group to
`$GITHUB_OUTPUT` so later steps can gate on it. This script is that primitive; the callers
supply the rules.

  - `.github/workflows/renovate-derived-files.yml` — two coupled groups: `python`
    (pyproject/uv.lock/requirements_lock.txt) and `bazel` (MODULE.bazel). A Python bump
    restales MODULE.bazel.lock's pip `facts`, so `--imply python=bazel` forces `bazel` true
    whenever `python` is.
  - `.github/workflows/devcontainer.yml` — one group, `changed`, over `.devcontainer/` and
    the workflow file itself.

Two decisions are load-bearing and live here so both callers inherit them:

  - **Three-dot diff.** `git diff <base>...HEAD` compares the merge base to HEAD, so a base
    that advanced under an open PR does not read as the branch's own changes. Two-dot
    (`git diff <base>`) would classify a Bazel-only PR as Python the moment `main` picked up
    a dep bump — and the renovate workflow would then commit unrelated lock upgrades onto it.
  - **Branch-creation short-circuit.** A push that creates a branch has an all-zero base SHA
    (`github.event.before`), which no diff can resolve. Treat it as "everything changed" so a
    first push still runs the gated work rather than silently skipping it.

The pure functions (`parse_rule`, `parse_implication`, `is_branch_creation`, `classify`,
`format_outputs`) carry all the non-I/O logic so the test suite exercises the moving parts
without git or the Actions environment — the same split as ratify_renovate_proposals.py.

Usage:
  python3 meta/scripts/classify_changed_paths.py --base <ref-or-sha> \\
    --rule '<name>=<regex>' [--rule ...] [--imply <source>=<target> ...]
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


def parse_implication(spec: str) -> tuple[str, str]:
    """Split a `source=target` implication spec into (source, target) group names."""
    source, sep, target = spec.partition("=")
    if not sep or not source or not target:
        raise ValueError(f"implication must be 'source=target', got: {spec!r}")
    return source, target


def is_branch_creation(base: str) -> bool:
    """True if `base` is git's all-zero null oid (a branch-creating push has no real base)."""
    return bool(_NULL_OID_RE.match(base))


def classify(
    files: list[str],
    rules: dict[str, str],
    implications: list[tuple[str, str]],
) -> dict[str, bool]:
    """Map each rule name to whether any changed file matches its regex, then apply implications.

    A rule matches if `re.search(pattern, path)` hits any path — the caller's patterns carry
    their own anchors. Implications run to a fixpoint (so chains like a→b, b→c all propagate):
    whenever the source group is true, the target is forced true.
    """
    result = {name: any(re.search(pattern, f) for f in files) for name, pattern in rules.items()}
    changed = True
    while changed:
        changed = False
        for source, target in implications:
            if result.get(source) and not result.get(target):
                result[target] = True
                changed = True
    return result


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
    parser.add_argument(
        "--imply",
        action="append",
        default=[],
        metavar="SOURCE=TARGET",
        help="Force TARGET true whenever SOURCE is true; repeatable.",
    )
    args = parser.parse_args(argv)

    rules = dict(parse_rule(r) for r in args.rule)
    implications = [parse_implication(i) for i in args.imply]

    # Every implication endpoint must be a declared rule, or its output is never emitted.
    for source, target in implications:
        for name in (source, target):
            if name not in rules:
                parser.error(f"--imply references undeclared rule {name!r}")

    if is_branch_creation(args.base):
        print(f"Base {args.base} is the null oid (branch creation); all groups treated as changed.")
        result = {name: True for name in rules}
    else:
        files = _git_changed_files(args.base)
        print(f"Changed vs {args.base}:")
        print("".join(f"  {f}\n" for f in files) or "  (none)\n", end="")
        result = classify(files, rules, implications)

    output = format_outputs(result)
    print("Classification:")
    print("".join(f"  {line}\n" for line in output.splitlines()), end="")
    _emit_github_output(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
