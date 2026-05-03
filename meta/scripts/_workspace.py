"""Shared helpers for the meta/scripts/ check_*.py guards.

Private to meta/scripts/ — not part of any public API. The leading underscore signals that
external callers should not depend on these helpers.
"""

import subprocess
from pathlib import Path

_SKIP_DIR_PREFIXES = ("bazel-",)
_SKIP_DIR_NAMES = {".git", "node_modules"}


def workspace_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    )
    return Path(result.stdout.strip())


def is_skipped(path: Path) -> bool:
    """True if any path component is .git, node_modules, or starts with bazel-.

    These directories are excluded from filesystem walks because they are either build
    output (bazel-*), VCS metadata (.git), or a foreign dependency tree (node_modules)
    that should never contain repo-managed sources.
    """
    return any(
        part in _SKIP_DIR_NAMES or part.startswith(_SKIP_DIR_PREFIXES)
        for part in path.parts
    )


def find_files(root: Path, pattern: str) -> list[Path]:
    """Recursively find files matching pattern under root, skipping is_skipped paths."""
    return [p for p in root.rglob(pattern) if not is_skipped(p)]


def found_modules(root: Path) -> set[Path]:
    """Return the set of directories containing a go.mod, relative to root."""
    return {p.parent.relative_to(root) for p in find_files(root, "go.mod")}


def registered_modules(root: Path) -> set[Path]:
    """Parse use directives from go.work, returning paths relative to root.

    go.work supports two syntactic forms for use directives:

      Unfactored:  use ./some/module
      Factored:    use (
                       ./some/module
                       ./other/module
                   )

    Both forms are handled by tracking whether we are currently inside a use (...) block. This is
    necessary to avoid false positives on factored replace directives, whose entries also begin with
    './' when they reference local paths (e.g. replace ( ./foo => ./bar )).

    Since this script runs at the monorepo root, all legitimate use paths are subdirectories and
    therefore begin with './'. Paths beginning with '../' would reference modules outside the
    repository entirely and are not expected here.

    Other top-level directives (go, toolchain) and single-line replace directives do not begin with
    './', so they are naturally ignored.
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
