"""Shared helpers for the meta/scripts/ check_*.py guards.

Private to meta/scripts/ — not part of any public API. The leading underscore signals that
external callers should not depend on these helpers.
"""

import subprocess
from pathlib import Path

_SKIP_DIR_PREFIXES = ("bazel-",)
_SKIP_DIR_NAMES = {".git", "node_modules", ".venv", "venv", ".git-plumbing"}


def workspace_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(result.stdout.strip())


def exit_status(errors: int) -> int:
    """Collapse a finding count to a pass/fail process status.

    A guard's exit status must never be the count itself. `sys.exit()` truncates it mod 256, so
    exactly 256 findings report success, and the shell reserves 126 (found, not executable), 127
    (not found) and 128+n (killed by signal n) — so any count from 126 up either misreports or
    impersonates a different failure. Nothing consumes the number anyway: CI reads pass/fail and
    the editor's problem matcher parses stdout. Guards keep the count as an ordinary return value
    for their tests and pass it through here at the process boundary.
    """
    return 1 if errors else 0


def is_skipped(path: Path) -> bool:
    """True if any component is .git, node_modules, .venv, .git-plumbing, or starts with bazel-.

    These directories are excluded from filesystem walks because they are either build
    output (bazel-*), VCS metadata (.git), virtualenv state (.venv/venv), host-state plumbing
    (.git-plumbing), or a foreign dependency tree (node_modules) that should never contain
    repo-managed sources.
    """
    return any(
        part in _SKIP_DIR_NAMES or part.startswith(_SKIP_DIR_PREFIXES) for part in path.parts
    )


def find_files(root: Path, pattern: str) -> list[Path]:
    """Recursively find files matching pattern under root, skipping is_skipped paths."""
    return [p for p in root.rglob(pattern) if not is_skipped(p)]


def find_go_modules(root: Path) -> set[Path]:
    """Return the set of directories containing a go.mod, relative to root."""
    return {p.parent.relative_to(root) for p in find_files(root, "go.mod")}


def find_python_projects(root: Path) -> set[Path]:
    """Return the set of subdirectories containing a pyproject.toml, relative to root.

    Excludes the root pyproject.toml itself (the uv workspace root is not a project; it carries
    only repo-wide tool config and the workspace member list).
    """
    return {
        p.parent.relative_to(root) for p in find_files(root, "pyproject.toml") if p.parent != root
    }


def col_range(file: Path, lineno: int, needle: str) -> tuple[int, int]:
    """1-based (startCol, endCol-exclusive) of `needle`'s first occurrence on `file`'s `lineno`.

    Lets check scripts emit `file:line:start-end:` diagnostics so the problem matcher
    squiggles the offending token, not just column 1. Falls back to (1, 2) — a one-char
    highlight — if the line or needle isn't where we expect, so a stale parse can't crash
    the diagnostic emit.
    """
    try:
        line = file.read_text().splitlines()[lineno - 1]
        start = line.index(needle) + 1
        return start, start + len(needle)
    except OSError, IndexError, ValueError:
        # bad path / past-EOF lineno / needle-not-on-line all collapse to the same fallback.
        return 1, 2


def registered_modules(root: Path) -> dict[Path, int]:
    """Parse use directives from go.work, returning {module_path: 1-based line in go.work}.

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

    Line numbers are retained so callers (e.g. check_go_work.py) can emit diagnostics that
    editors with a problem matcher will resolve into squiggles at the offending line. Callers
    that only need the set of paths can iterate the dict directly (or wrap in `set(...)`).
    """
    go_work = root / "go.work"
    modules: dict[Path, int] = {}
    in_use_block = False

    for lineno, line in enumerate(go_work.read_text().splitlines(), start=1):
        stripped = line.strip()

        if stripped == "use (":
            in_use_block = True
            continue

        if in_use_block:
            if stripped == ")":
                in_use_block = False
            elif stripped.startswith("./"):
                modules[Path(stripped[2:])] = lineno
            continue

        # Single-line form: use ./some/module
        if stripped.startswith("use ./"):
            modules[Path(stripped[6:])] = lineno

    return modules
