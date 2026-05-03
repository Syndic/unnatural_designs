#!/usr/bin/env python3
"""
Counts py_library, py_binary, and py_test target declarations across all BUILD.bazel files in the
repo. Fails if the count exceeds THRESHOLD, as a signal to evaluate enabling Gazelle's Python
plugin.

When this check fails, do one of:
  - Enable Gazelle Python support (see rules_python docs for Gazelle integration)
  - Raise THRESHOLD below with a comment explaining why it is still not warranted

Usage: ./meta/scripts/check_python_scale.py
"""

import re
import sys
from pathlib import Path

# When invoked as `python3 meta/scripts/check_python_scale.py` (the form used in CI and by
# pre-commit), the workspace root is not on sys.path, so `from meta.scripts.X` would fail.
# Adding the workspace root explicitly fixes that and is harmless under bazel py_binary,
# where rules_python already makes the import resolvable.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from meta.scripts._workspace import find_files, workspace_root  # noqa: E402

# DO NOT BUMP THIS VALUE WITHOUT EXPLICIT OWNER APPROVAL.
#
# This threshold exists to get the owner's attention. When the count of py_* targets
# crosses it, the owner evaluates whether it is time to adopt Gazelle (or some other
# scaling step). A failing check IS the signal — silently raising THRESHOLD to make CI green
# defeats the entire mechanism.
#
# If you hit this failure: stop, surface it, and ask the owner.
# Do not "fix" it by editing this number; that hides the signal.
# Given an overeager LLM's attempt to "fix an issue without bothering anyone about it", CODEOWNERS
# now guards this file so the change cannot be merged without the owner's review.
THRESHOLD = 22

# Matches py_library(, py_binary(, py_test( at the start of a non-comment line.
_PYTHON_TARGET_RE = re.compile(r"^\s*(py_library|py_binary|py_test)\s*\(")


def count_python_targets(root: Path) -> dict[Path, int]:
    """Count py_* target declarations per BUILD.bazel file, relative to root.

    Comment lines (those whose first non-whitespace character is #) are excluded before matching,
    so commented-out targets do not inflate the count. Bazel output symlinks (bazel-*), .git, and
    node_modules are excluded.
    """
    counts: dict[Path, int] = {}
    for build_file in find_files(root, "BUILD.bazel"):
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
