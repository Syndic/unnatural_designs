#!/usr/bin/env python3
"""Prevents files other than secrets/secrets.md from being committed to //secrets.

Two entry points over one allowlist:
  - given filenames (pre-commit): checks exactly the staged paths it is handed
  - given none (CI): enumerates the tracked contents of secrets/ itself, so the gate still
    fires on a commit made with the hooks bypassed or never installed

Usage: ./meta/scripts/check_secrets_dir.py             # check every tracked file under secrets/
       ./meta/scripts/check_secrets_dir.py <file> ...  # check the named files
"""

import subprocess
import sys
from pathlib import Path

# When invoked as `python3 meta/scripts/check_secrets_dir.py` (the form used in CI and by
# pre-commit), the workspace root is not on sys.path, so `from meta.scripts.X` would fail.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from meta.scripts._workspace import exit_status, workspace_root

_SECRETS_DIR = "secrets"

_ALLOWED = frozenset([Path("secrets/secrets.md")])


def tracked_files() -> list[Path]:
    """Every path git tracks under secrets/, relative to the workspace root.

    Tracked rather than on-disk: `.gitignore` carries `secrets/*`, so the directory holds a
    developer's real secrets locally and a filesystem walk would report those as violations.
    """
    result = subprocess.run(
        ["git", "ls-files", "--", _SECRETS_DIR],
        capture_output=True,
        text=True,
        check=True,
        cwd=workspace_root(),
    )
    return [Path(line) for line in result.stdout.splitlines() if line]


def run(files: list[Path]) -> int:
    violations = [f for f in files if f not in _ALLOWED]
    for f in violations:
        print(f"ERROR: {f} must not be committed to //secrets")
    return len(violations)


def main() -> int:
    args = sys.argv[1:]
    files = [Path(a) for a in args] if args else tracked_files()
    return exit_status(run(files))


if __name__ == "__main__":
    sys.exit(main())
