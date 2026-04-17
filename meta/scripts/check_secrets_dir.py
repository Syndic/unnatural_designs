#!/usr/bin/env python3
"""Prevents files other than secrets/secrets.md from being committed to //secrets.

Called by pre-commit with the list of staged files matching secrets/. Fails if
any file other than the placeholder markdown is about to be committed there.

Usage: ./meta/scripts/check_secrets_dir.py <file> [<file> ...]
"""

import sys
from pathlib import Path

_ALLOWED = frozenset([Path("secrets/secrets.md")])


def main() -> int:
    violations = [f for f in sys.argv[1:] if Path(f) not in _ALLOWED]
    for f in violations:
        print(f"ERROR: {f} must not be committed to //secrets")
    return len(violations)


if __name__ == "__main__":
    sys.exit(main())
