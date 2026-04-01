#!/usr/bin/env python3
"""
Verifies that go.work is consistent with the Go modules in this repo:
  - Every go.mod file must have a corresponding use entry in go.work
  - Every use entry in go.work must point to a directory with a go.mod

Usage: ./meta/scripts/check_go_work.py
"""

import subprocess
import sys
from pathlib import Path


def workspace_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True
    )
    return Path(result.stdout.strip())


def registered_modules(root: Path) -> set[Path]:
    """Parse use directives from go.work, returning paths relative to root.

    go.work supports two syntactic forms for use directives:

      Unfactored:  use ./some/module
      Factored:    use (
                       ./some/module
                       ./other/module
                   )

    Both forms are handled by tracking whether we are currently inside a
    use (...) block. This is necessary to avoid false positives on factored
    replace directives, whose entries also begin with './' when they reference
    local paths (e.g. replace ( ./foo => ./bar )).

    Since this script runs at the monorepo root, all legitimate use paths are
    subdirectories and therefore begin with './'. Paths beginning with '../'
    would reference modules outside the repository entirely and are not
    expected here.

    Other top-level directives (go, toolchain) and single-line replace
    directives do not begin with './', so they are naturally ignored.
    """
    go_work = root / "go.work"
    modules = set()
    in_use_block = False

    for line in go_work.read_text().splitlines():
        stripped = line.strip()

        if stripped == "use (":
            in_use_block = True
            continue

        if in_use_block:
            if stripped == ")":
                in_use_block = False
            elif stripped.startswith("./"):
                modules.add(Path(stripped[2:]))
            continue

        # Single-line form: use ./some/module
        if stripped.startswith("use ./"):
            modules.add(Path(stripped[6:]))

    return modules


def found_modules(root: Path) -> set[Path]:
    """Find all go.mod files, excluding Bazel output symlinks and .git."""
    return {
        p.parent.relative_to(root)
        for p in root.rglob("go.mod")
        if not any(part.startswith("bazel-") or part == ".git" for part in p.parts)
    }


def main() -> int:
    root = workspace_root()
    registered = registered_modules(root)
    found = found_modules(root)

    errors = 0

    for mod in sorted(found - registered):
        print(f"MISSING from go.work: ./{mod} has a go.mod but no use entry")
        errors += 1

    for mod in sorted(registered - found):
        print(f"STALE in go.work: use ./{mod} has no go.mod")
        errors += 1

    if errors == 0:
        print("go.work is consistent with all go.mod files.")

    return errors


if __name__ == "__main__":
    sys.exit(main())
