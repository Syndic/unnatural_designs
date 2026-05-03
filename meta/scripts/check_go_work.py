#!/usr/bin/env python3
"""
Verifies that go.work is consistent with the Go modules in this repo:
  - Every go.mod file must have a corresponding use entry in go.work
  - Every use entry in go.work must point to a directory with a go.mod

Usage: ./meta/scripts/check_go_work.py
"""

import sys
from pathlib import Path

# When invoked as `python3 meta/scripts/check_go_work.py` (the form used in CI and by
# pre-commit), the workspace root is not on sys.path, so `from meta.scripts.X` would fail.
# Adding the workspace root explicitly fixes that and is harmless under bazel py_binary,
# where rules_python already makes the import resolvable.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from meta.scripts._workspace import (  # noqa: E402
    found_modules,
    registered_modules,
    workspace_root,
)


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
