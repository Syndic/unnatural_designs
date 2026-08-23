"""Shared helpers for the meta/scripts/ check_*.py guards.

Private to meta/scripts/ — not part of any public API. The leading underscore signals that
external callers should not depend on these helpers.
"""

import re
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


MatrixBlock = tuple[str, int, dict[Path, int]]


def _scan_matrices(workflow_file: Path, matrix_key: str) -> tuple[list[MatrixBlock], set[int]]:
    """Shared state machine behind the two public matrix helpers.

    Returns the parsed blocks and the set of line numbers on which ``matrix_key`` was
    recognised as a mapping key. The second half is what lets `unrecognised_matrix_keys`
    tell "no matrix here" apart from "a matrix this parser could not read" — the two are
    indistinguishable from the block list alone, and conflating them is what let a matrix
    in an unsupported shape pass as absent.
    """
    text = workflow_file.read_text()
    blocks: list[MatrixBlock] = []
    recognised: set[int] = set()

    key_line_marker = f"{matrix_key}:"
    # `include:` entries carry the key on the item line itself (`- go_module: tools/foo`) or on
    # a following line of the same item, once a second dimension puts another key first.
    item_key_re = re.compile(rf"^-\s+{re.escape(matrix_key)}:\s*(\S+)\s*$")
    cont_key_re = re.compile(rf"^{re.escape(matrix_key)}:\s*(\S+)\s*$")

    state = "scanning"  # scanning | in_matrix | in_entries | in_include
    matrix_indent = -1
    entries_indent = -1
    include_indent = -1

    # Accumulated per `matrix:` block rather than per key, because GitHub unions a plain list
    # with an `include:` in the same matrix — emitting those as two blocks would report each
    # one's entries as missing from the other.
    key_line = -1
    saw_key = False
    current: dict[Path, int] = {}
    matrix_job = "<unknown>"

    in_jobs = False
    current_job = "<unknown>"

    def note_key(lineno: int) -> None:
        nonlocal key_line, saw_key
        saw_key = True
        if key_line == -1:
            key_line = lineno
        recognised.add(lineno)

    def close_matrix() -> None:
        nonlocal key_line, saw_key, current
        if saw_key:
            blocks.append((matrix_job, key_line, current))
        key_line = -1
        saw_key = False
        current = {}

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

        # ── Guards: dedent out of each nested block, innermost first ───────
        if state == "in_entries" and indent <= entries_indent:
            state = "in_matrix"
            entries_indent = -1
        if state == "in_include" and indent <= include_indent:
            state = "in_matrix"
            include_indent = -1
        if state == "in_matrix" and indent <= matrix_indent:
            close_matrix()
            state = "scanning"
            matrix_indent = -1

        # ── Process line in current state ──────────────────────────────────
        if state == "scanning":
            if stripped == "matrix:":
                state = "in_matrix"
                matrix_indent = indent
                # Captured on entry, not on exit: the line that closes a matrix can be the
                # next job's key, which has already moved current_job by the time we get here.
                matrix_job = current_job

        elif state == "in_matrix":
            if stripped == key_line_marker:
                state = "in_entries"
                entries_indent = indent
                note_key(lineno)
            elif stripped == "include:":
                state = "in_include"
                include_indent = indent

        elif state == "in_entries" and stripped.startswith("- "):
            current[Path(stripped[2:].strip())] = lineno

        elif state == "in_include":
            match = item_key_re.match(stripped) or cont_key_re.match(stripped)
            if match:
                current[Path(match.group(1).strip("\"'"))] = lineno
                note_key(lineno)

    # End of file while still inside a matrix.
    if state in ("in_matrix", "in_entries", "in_include"):
        close_matrix()

    return blocks, recognised


def workflow_matrix_lists(workflow_file: Path, matrix_key: str) -> list[MatrixBlock]:
    """Parse a GitHub Actions workflow and return all `matrix.<matrix_key>` lists with line numbers.

    matrix_key is the YAML key the matrix block uses (e.g. ``"go_module"`` for Go's
    per-module matrices, ``"python_project"`` for a future Python equivalent). The
    key string is supplied per-language so a single workflow file can carry both
    Go and Python matrices without the parser conflating them — see check_modules.py's
    LanguageSpec.matrix_key.

    Returns one ``(job_name, key_line, {entry_path: line_number})`` tuple per `matrix:` block
    that names the key. ``key_line`` is the 1-based line the key was first seen on (the anchor
    used by callers for "missing entry" diagnostics, which have no specific offending line).
    Each entry maps a path to the 1-based line it was read from.

    Two forms are recognised, and a matrix using both has its entries unioned into one block,
    matching how GitHub combines them::

        jobs:
          <job-key>:                  # indent 2  — recorded as job_name
            strategy:
              matrix:                 # marks start of matrix block
                <matrix_key>:         # marks start of entry list — line recorded as key_line
                  - some/path         # collected as a {Path: line_number} entry
                include:              # the second form, used once a matrix has another axis
                  - <matrix_key>: some/path
                    other_axis: value

    Anything else that names the key — a flow sequence, a `fromJSON` expression, a quoted key —
    yields no block, which is why `unrecognised_matrix_keys` exists to catch it rather than
    letting it read as "this workflow has no matrix". Parsing is line-oriented and indent-aware
    with no third-party dependencies; line numbers are retained so callers can emit
    `file:line: message` diagnostics that VS Code's problem matcher surfaces as squiggles.
    """
    blocks, _ = _scan_matrices(workflow_file, matrix_key)
    return blocks


# A mapping key, with the optional `- ` of a sequence item and optional quoting. Deliberately
# stops at the colon: what follows is exactly what this is meant to catch, in every spelling.
def _key_occurrence_re(matrix_key: str) -> re.Pattern[str]:
    return re.compile(rf"""^(?:-\s+)?["']?{re.escape(matrix_key)}["']?\s*:""")


def unrecognised_matrix_keys(workflow_file: Path, matrix_key: str) -> dict[int, str]:
    """Lines naming ``matrix_key`` as a mapping key that `workflow_matrix_lists` did not read.

    The parser's failure mode is silent under-detection: a shape it does not handle produces no
    block, and a caller iterating blocks sees nothing to complain about. So rather than chase
    each spelling, hold the parser to its contract — every textual occurrence of the key should
    correspond to a block it built. The ones that don't are returned here, keyed by line number,
    so the caller can fail loudly instead of passing by default.

    This is what makes an unsupported shape a decision rather than an accident: `go_module: []`,
    `go_module: [a, b]`, a quoted key, and a `fromJSON` expression all land here. The last of
    those cannot be resolved statically at all, which is the point — it needs a person, not a
    parser.
    """
    _, recognised = _scan_matrices(workflow_file, matrix_key)
    occurrence_re = _key_occurrence_re(matrix_key)

    found: dict[int, str] = {}
    for lineno, raw in enumerate(workflow_file.read_text().splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if occurrence_re.match(stripped) and lineno not in recognised:
            found[lineno] = stripped
    return found
