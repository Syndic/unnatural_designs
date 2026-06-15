#!/usr/bin/env python3
"""
Verifies cross-language module/project invariants:

  Per language (Go, Python):
    1. Every discovered module/project has a config file reachable via the language's
       config-search semantics (per-module file or any ancestor up to the repo root).
    2. Every matrix.module list in .github/workflows/*.yml that names language modules
       contains exactly the discovered set (no missing, no stale).

  Python-only:
    3. Root pyproject.toml has [tool.uv.workspace], [tool.ruff], and [tool.ty] sections —
       the single canonical home for workspace + tool config.
    4. Every discovered per-project pyproject.toml matches a glob in [tool.uv.workspace].members
       so workspace resolution and member iteration agree.
    5. requirements_lock.txt is fresh relative to uv.lock (cheap diff via `uv export`).

Polyglot replacement for the former check_go_modules.py; matrix-list parsing remains
Go-specific in practice because today only Go uses matrix.module:. Python adds its checks
without sharing a matrix-list contract until a Python project adopts matrices of its own.

TEND(lang-expand): adopting a new language means adding an entry to LANGUAGES (discovery
fn + config-file names + module anchor) and, if that language drives matrix-per-module CI
jobs, adding a parallel matrix-completeness callsite parameterized by its matrix key
(matrix.module: is currently Go's; a Python equivalent would use a different key to keep
the two from colliding).

Usage: ./meta/scripts/check_modules.py
"""

import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

# When invoked as `python3 meta/scripts/check_modules.py` (the form used in CI and by
# pre-commit), the workspace root is not on sys.path, so `from meta.scripts.X` would fail.
# Adding the workspace root explicitly fixes that and is harmless under bazel py_binary,
# where rules_python already makes the import resolvable.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from meta.scripts._workspace import (
    col_range,
    find_go_modules,
    find_python_projects,
    workflow_module_lists,
    workspace_root,
)


@dataclass(frozen=True)
class LanguageSpec:
    """Drives the polyglot per-language checks.

    name           — short identifier used in diagnostics.
    discover       — returns {module_path: ...} relative to root.
    config_files   — filenames satisfying the per-module config check. A module is
                     considered configured if any of these names exists in the module
                     directory or any ancestor up to the repo root (matching the search
                     semantics of golangci-lint and Python's nearest-pyproject).
    module_anchor  — relative filename within a module used to anchor "missing config"
                     diagnostics on a file that actually exists (e.g. go.mod, pyproject.toml).
    """

    name: str
    discover: Callable[[Path], set[Path]]
    config_files: tuple[str, ...]
    module_anchor: str


LANGUAGES: tuple[LanguageSpec, ...] = (
    LanguageSpec(
        name="go",
        discover=find_go_modules,
        config_files=(".golangci.yml",),
        module_anchor="go.mod",
    ),
    LanguageSpec(
        name="python",
        discover=find_python_projects,
        config_files=("pyproject.toml",),
        module_anchor="pyproject.toml",
    ),
)


# ── Polyglot checks ───────────────────────────────────────────────────────────


def check_module_configs(root: Path, language: LanguageSpec, modules: set[Path]) -> int:
    """Verify every discovered module has a config file reachable from its directory upward.

    Mirrors golangci-lint's config search: walk the module directory toward the repo root,
    accept the first ancestor that contains any of language.config_files. A root-level config
    is valid shared config for every module below it.

    Python's per-project pyproject.toml is trivially reachable from its own directory — the
    check would always pass on `pyproject.toml`-keyed languages, but the symmetry is worth the
    cycle and the bug class it catches is real (a project pyproject deleted by a misfire).
    """
    errors = 0
    for mod in sorted(modules):
        candidate = root / mod
        found = False
        while True:
            if any((candidate / name).is_file() for name in language.config_files):
                found = True
                break
            if candidate == root:
                break
            candidate = candidate.parent
        if not found:
            # Anchor on the module's manifest file — no specific token at fault.
            names = " or ".join(language.config_files)
            print(
                f"{mod}/{language.module_anchor}:1:1-2: no {names} reachable from ./{mod} "
                f"(module dir or any parent up to repo root)"
            )
            errors += 1
    return errors


def check_workflow_matrices(root: Path, modules: set[Path]) -> int:
    """Check all .github/workflows/*.yml files for module matrix completeness.

    Iterates every `matrix.module:` block found across all workflow files and verifies the
    list matches the discovered module set. Language-agnostic by structure but Go-de-facto:
    today only Go uses `matrix.module:` (per-Python-project matrices would need a separate
    key — `matrix.project:` or similar — and a separate caller).
    """
    workflows_dir = root / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return 0

    errors = 0
    for wf_file in sorted(workflows_dir.glob("*.yml")):
        rel = str(wf_file.relative_to(root))
        for job_name, module_key_line, matrix_entries in workflow_module_lists(wf_file):
            matrix_set = set(matrix_entries)
            for mod in sorted(modules - matrix_set):
                print(f"{rel}:{module_key_line}:1-2: [{job_name}] missing matrix entry for ./{mod}")
                errors += 1
            for mod in sorted(matrix_set - modules):
                line = matrix_entries[mod]
                start, end = col_range(wf_file, line, str(mod))
                print(
                    f"{rel}:{line}:{start}-{end}: "
                    f"[{job_name}] stale matrix entry ./{mod} (no module manifest)"
                )
                errors += 1

    return errors


# ── Python-specific invariants ────────────────────────────────────────────────


_REQUIRED_PYPROJECT_SECTIONS: tuple[tuple[str, ...], ...] = (
    ("tool", "uv", "workspace"),
    ("tool", "ruff"),
    ("tool", "ty"),
)


def _has_section(data: dict, path: tuple[str, ...]) -> bool:
    cur: object = data
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return False
        cur = cur[key]
    return True


def check_python_workspace_root(root: Path) -> int:
    """Verify root pyproject.toml exists, parses, and carries the required tool config.

    The three sections are the contract this repo's Python tooling stands on: uv workspace
    membership, ruff format/lint config, and ty type-check config. Each was placed at the
    root deliberately as the single canonical home — surfacing a missing one before it can
    silently rot is the whole point of this check.
    """
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        print("pyproject.toml:1:1-2: root pyproject.toml missing")
        return 1

    try:
        data = tomllib.loads(pyproject.read_text())
    except tomllib.TOMLDecodeError as e:
        print(f"pyproject.toml:1:1-2: failed to parse root pyproject.toml ({e})")
        return 1

    errors = 0
    for section in _REQUIRED_PYPROJECT_SECTIONS:
        if not _has_section(data, section):
            dotted = ".".join(section)
            print(f"pyproject.toml:1:1-2: missing [{dotted}] in root pyproject.toml")
            errors += 1
    return errors


def check_python_workspace_members(root: Path, projects: set[Path]) -> int:
    """Verify every discovered per-project pyproject.toml matches a glob in workspace.members.

    `uv` resolves workspace members from the `[tool.uv.workspace].members` list (each entry a
    path or glob, relative to the root). If a project pyproject lives on disk but is not in
    that list, uv will not include it in the workspace lock — a silent divergence that this
    check catches at CI time.
    """
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return 0  # already reported by check_python_workspace_root

    try:
        data = tomllib.loads(pyproject.read_text())
    except tomllib.TOMLDecodeError:
        return 0  # already reported

    cur: object = data
    for key in ("tool", "uv", "workspace", "members"):
        if not isinstance(cur, dict) or key not in cur:
            return 0
        cur = cur[key]
    if not isinstance(cur, list):
        return 0
    members = cur

    errors = 0
    for project in sorted(projects):
        project_str = str(project)
        if not any(fnmatch(project_str, glob) for glob in members):
            print(
                f"{project}/pyproject.toml:1:1-2: not covered by any glob in "
                f"[tool.uv.workspace].members (root pyproject.toml)"
            )
            errors += 1
    return errors


def _strip_header(text: str) -> str:
    """Drop leading ``#``-prefixed lines.

    `uv export` writes a two-line header that embeds the literal `--output-file` path used
    on the invocation, so a check-run header will never match the hook-run header even when
    the package list is identical. Skipping leading top-level comment lines on both sides
    lets the comparison focus on the package contents. `# via` annotations on per-package
    lines are leading-whitespace-prefixed and not dropped here.
    """
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines) and lines[i].startswith("#"):
        i += 1
    return "".join(lines[i:])


def _uv_export(root: Path) -> str | None:
    """Run `uv export` and return what it would write to requirements_lock.txt.

    Writes to a tempfile and reads it back instead of capturing stdout — header-comment
    stripping in :func:`_strip_header` handles the invocation-path difference at compare
    time. Returns None if uv is not on PATH (skipped silently — the pre-commit hook is the
    durable enforcement; this check is the CI safety net).
    """
    with tempfile.NamedTemporaryFile(mode="w+", suffix=".txt", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        try:
            subprocess.run(
                [
                    "uv",
                    "export",
                    "--format",
                    "requirements-txt",
                    "--no-hashes",
                    "--no-emit-project",
                    "--output-file",
                    str(tmp_path),
                ],
                cwd=root,
                capture_output=True,
                text=True,
                check=True,
            )
        except FileNotFoundError:
            return None
        return tmp_path.read_text()
    finally:
        tmp_path.unlink(missing_ok=True)


def check_uv_lock_fresh(root: Path) -> int:
    """Cheap freshness check: re-export the lock and diff against the checked-in file.

    Mirrors the `uv-lock-fresh` pre-commit hook's export invocation exactly so the two
    cannot disagree about what "fresh" means. The full re-lock (`uv lock`) lives in the
    hook alone — running it in CI would cost network and resolution time we already spend
    at commit time. The export step is local-only and fast.
    """
    checked_in = root / "requirements_lock.txt"
    if not checked_in.is_file():
        print("requirements_lock.txt:1:1-2: missing — `uv export` has never been run")
        return 1

    try:
        exported = _uv_export(root)
    except subprocess.CalledProcessError as e:
        print(f"requirements_lock.txt:1:1-2: `uv export` failed: {e.stderr.strip() or e}")
        return 1

    if exported is None:
        return 0
    if _strip_header(exported) != _strip_header(checked_in.read_text()):
        print(
            "requirements_lock.txt:1:1-2: stale — out of sync with uv.lock + pyproject.toml. "
            "Re-run `uv export --format requirements-txt --no-hashes --no-emit-project "
            "--output-file requirements_lock.txt` (or rely on the uv-lock-fresh pre-commit hook)."
        )
        return 1
    return 0


# ── Driver ────────────────────────────────────────────────────────────────────


def main() -> int:
    root = workspace_root()

    errors = 0
    for language in LANGUAGES:
        modules = language.discover(root)
        errors += check_module_configs(root, language, modules)
        # Workflow matrix completeness is gated by language: today only Go has matrix-driven
        # per-module jobs. When a Python language adopts matrix.<something>:, a parallel
        # callsite parameterized by that key is the right shape.
        if language.name == "go":
            errors += check_workflow_matrices(root, modules)

    errors += check_python_workspace_root(root)
    errors += check_python_workspace_members(root, find_python_projects(root))
    errors += check_uv_lock_fresh(root)

    if errors == 0:
        print("All modules and workspace invariants are consistent.")
    return errors


if __name__ == "__main__":
    sys.exit(main())
