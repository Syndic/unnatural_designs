#!/usr/bin/env python3
"""
Counts py_library, py_binary, and py_test target declarations across all
BUILD.bazel files in the repo. Fails if the count exceeds THRESHOLD, as a
signal to evaluate enabling Gazelle's Python plugin.

When this check fails, do one of:
  - Enable Gazelle Python support (see rules_python docs for Gazelle integration)
  - Raise THRESHOLD below with a comment explaining why it is still not warranted

Usage: ./meta/scripts/check_python_scale.py
"""

import re
import subprocess
import sys
from pathlib import Path

THRESHOLD = 15

# Matches py_library(, py_binary(, py_test( at the start of a non-comment line.
_PYTHON_TARGET_RE = re.compile(r"^\s*(py_library|py_binary|py_test)\s*\(")


def workspace_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    )
    return Path(result.stdout.strip())


def count_python_targets(root: Path) -> dict[Path, int]:
    """Count py_* target declarations per BUILD.bazel file, relative to root.

    Comment lines (those whose first non-whitespace character is #) are
    excluded before matching, so commented-out targets do not inflate the count.
    Bazel output symlinks (bazel-*) and .git are excluded.
    """
    counts: dict[Path, int] = {}
    for build_file in root.rglob("BUILD.bazel"):
        if any(part.startswith("bazel-") or part == ".git" for part in build_file.parts):
            continue
        active_lines = [
            line for line in build_file.read_text().splitlines()
            if not line.lstrip().startswith("#")
        ]
        count = sum(1 for line in active_lines if _PYTHON_TARGET_RE.match(line))
        if count > 0:
            counts[build_file.relative_to(root)] = count
    return counts


def check(root: Path, threshold: int = THRESHOLD) -> int:
    counts = count_python_targets(root)
    total = sum(counts.values())

    print(f"Python Bazel targets: {total} / {threshold}")
    for path, count in sorted(counts.items()):
        print(f"  {path}: {count}")

    if total > threshold:
        print(
            f"\nThreshold of {threshold} exceeded. Either enable Gazelle Python "
            f"support or raise THRESHOLD in check_python_scale.py with a comment "
            f"explaining why it is still not warranted."
        )
        return 1

    return 0


def main() -> int:
    return check(workspace_root())


if __name__ == "__main__":
    sys.exit(main())
