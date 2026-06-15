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


def workflow_module_lists(
    workflow_file: Path,
) -> list[tuple[str, int, dict[Path, int]]]:
    """Parse a GitHub Actions workflow file and return all matrix.module lists with line numbers.

    Returns one (job_name, module_key_line, {module_path: line_number}) tuple per matrix.module
    block found. module_key_line is the 1-based line of the `module:` key (the anchor used by
    callers for "missing entry" diagnostics, which have no specific offending line). Each entry
    in the dict maps a module path to the 1-based line of its `- path` list item.

    job_name is the YAML job key, captured on a best-effort basis by tracking keys at indent 2
    inside the jobs: block; falls back to '<unknown>'. Parsing is line-oriented and indent-aware,
    with no third-party dependencies. The GitHub Actions YAML structure is regular enough to
    make this reliable:

        jobs:
          <job-key>:           # indent 2  — recorded as job_name
            strategy:
              matrix:          # marks start of matrix block
                module:        # marks start of module list — line recorded as module_key_line
                  - some/path  # collected as a {Path: line_number} entry

    The parser uses two guard checks that fire before each line is processed, handling dedent
    out of the module list and out of the matrix block. This avoids re-processing lines across
    state transitions. Line numbers are retained so callers can emit `file:line: message`
    diagnostics that VS Code's problem matcher surfaces as squiggles.

    Polyglot note: the function is language-agnostic — it parses `matrix.module:` lists
    regardless of whether they hold Go module paths or any other identifier. Callers
    parameterize meaning via the discovered-module set they compare against.
    """
    text = workflow_file.read_text()
    result: list[tuple[str, int, dict[Path, int]]] = []

    state = "scanning"  # scanning | in_matrix | in_module
    matrix_indent = -1
    module_indent = -1
    module_key_line = -1
    current: dict[Path, int] | None = None

    # Job name tracking: record the YAML key at indent 2 inside jobs:.
    in_jobs = False
    current_job = "<unknown>"

    for lineno, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())

        # ── Job name tracking ──────────────────────────────────────────────
        if stripped == "jobs:" and indent == 0:
            in_jobs = True
        elif in_jobs:
            if indent == 0:
                in_jobs = False
            elif indent == 2 and stripped.endswith(":"):
                current_job = stripped[:-1]

        # ── Guard: leaving module list ─────────────────────────────────────
        # Any line at or above the module: key's indent signals that the list is over. Save the
        # accumulated entries and decide which state to return to based on whether we're still
        # inside the matrix block.
        if state == "in_module" and indent <= module_indent:
            if current is not None:
                result.append((current_job, module_key_line, current))
            current = None
            module_indent = -1
            module_key_line = -1
            state = "in_matrix" if indent > matrix_indent else "scanning"
            if state == "scanning":
                matrix_indent = -1

        # ── Guard: leaving matrix block ────────────────────────────────────
        if state == "in_matrix" and indent <= matrix_indent:
            state = "scanning"
            matrix_indent = -1

        # ── Process line in current state ──────────────────────────────────
        if state == "scanning":
            if stripped == "matrix:":
                state = "in_matrix"
                matrix_indent = indent

        elif state == "in_matrix":
            if stripped == "module:":
                state = "in_module"
                module_indent = indent
                module_key_line = lineno
                current = {}

        elif state == "in_module" and stripped.startswith("- "):
            current[Path(stripped[2:].strip())] = lineno

    # End of file while still inside a module list.
    if state == "in_module" and current is not None:
        result.append((current_job, module_key_line, current))

    return result
