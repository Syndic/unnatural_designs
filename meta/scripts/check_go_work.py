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
    registered_locs = registered_modules(root)
    registered = set(registered_locs)
    found = found_modules(root)

    errors = 0

    # Diagnostics are formatted as `path:line: message` so a VS Code task with a problem
    # matcher (.vscode/tasks.json) can turn each line into a squiggle at the offending site.
    # Missing-entry diagnostics anchor on the module's go.mod (the file whose existence is
    # the trigger); stale-entry diagnostics anchor on the offending line in go.work.
    for mod in sorted(found - registered):
        print(f"{mod}/go.mod:1: missing from go.work: ./{mod} has a go.mod but no use entry")
        errors += 1

    for mod in sorted(registered - found):
        line = registered_locs[mod]
        print(f"go.work:{line}: stale entry: use ./{mod} has no go.mod")
        errors += 1

    if errors == 0:
        print("go.work is consistent with all go.mod files.")

    return errors


if __name__ == "__main__":
    sys.exit(main())
