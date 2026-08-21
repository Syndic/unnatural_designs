#!/usr/bin/env python3
"""
Verifies that ADR numbers are unique across every decision-record directory in the repo:
  - Every file in a docs/adr/ directory is named NNNN-kebab-slug.md (README.md excepted)
  - No two ADRs anywhere in the repo share a number

Numbering is repo-global, not per-directory: a context's ADR takes the next number across
*all* of docs/adr/ and <context>/docs/adr/. See docs/agents/domain.md.

Usage: ./meta/scripts/check_adr_numbers.py
"""

import re
import sys
from collections import defaultdict
from pathlib import Path

# When invoked as `python3 meta/scripts/check_adr_numbers.py` (the form used in CI and by the
# editor task), the workspace root is not on sys.path, so `from meta.scripts.X` would fail.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from meta.scripts._workspace import find_files, workspace_root

# Four digits so a plain sort orders them; kebab slug so filenames stay greppable and
# case-insensitive filesystems can't collide two ADRs onto one path.
_ADR_NAME_RE = re.compile(r"^(\d{4})-[a-z0-9]+(?:-[a-z0-9]+)*\.md$")

# A directory-level readme is not a decision record. Nothing else earns an exemption:
# an unrecognised file here is either a misnamed ADR or something in the wrong place.
_EXEMPT = frozenset(["README.md"])


def adr_files(root: Path) -> list[Path]:
    """Every markdown file under a docs/adr/ directory, sorted, relative to root."""
    return sorted(
        p.relative_to(root)
        for p in find_files(root, "*.md")
        if p.parent.name == "adr" and p.parent.parent.name == "docs"
    )


def violations(files: list[Path]) -> list[tuple[Path, str]]:
    """(path, message) for every malformed name and every reused number."""
    found: list[tuple[Path, str]] = []
    by_number: dict[str, list[Path]] = defaultdict(list)

    for path in files:
        if path.name in _EXEMPT:
            continue
        match = _ADR_NAME_RE.match(path.name)
        if match is None:
            found.append(
                (path, f"not a valid ADR filename: expected NNNN-kebab-slug.md, got {path.name}")
            )
            continue
        by_number[match.group(1)].append(path)

    for number, paths in by_number.items():
        if len(paths) == 1:
            continue
        first, *rest = paths
        for path in rest:
            found.append((path, f"duplicate ADR number {number}: already used by {first}"))

    return sorted(found)


def main() -> int:
    root = workspace_root()
    files = adr_files(root)
    found = violations(files)

    for path, message in found:
        # `path:line:startCol-endCol: message`, the format the editor's problem matcher reads.
        # The offence is the filename, so there is no interesting position inside the file.
        print(f"{path}:1:1-2: {message}")

    if not found:
        print(f"ADR numbers are unique across {len(files)} decision record(s).")

    return len(found)


if __name__ == "__main__":
    sys.exit(main())
