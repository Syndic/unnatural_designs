#!/usr/bin/env python3
"""
Verifies that all Go modules in the repo are consistently tracked:

  1. Every matrix.module list in .github/workflows/*.yml contains exactly
     the set of Go modules known from go.mod discovery (no missing, no stale).
  2. Every Go module has a .golangci.yml reachable via golangci-lint's config
     search (per-module file or a shared root-level fallback).

Usage: ./meta/scripts/check_go_modules.py
"""

import sys
from pathlib import Path

# When invoked as `python3 meta/scripts/check_go_modules.py` (the form used in CI and by
# pre-commit), the workspace root is not on sys.path, so `from meta.scripts.X` would fail.
# Adding the workspace root explicitly fixes that and is harmless under bazel py_binary,
# where rules_python already makes the import resolvable.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from meta.scripts._workspace import found_modules, workspace_root  # noqa: E402


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
    """
    text = workflow_file.read_text()
    result: list[tuple[str, int, dict[Path, int]]] = []

    state = "scanning"   # scanning | in_matrix | in_module
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

        elif state == "in_module":
            if stripped.startswith("- "):
                current[Path(stripped[2:].strip())] = lineno

    # End of file while still inside a module list.
    if state == "in_module" and current is not None:
        result.append((current_job, module_key_line, current))

    return result


def check_workflow_matrices(root: Path, modules: set[Path]) -> int:
    """Check all .github/workflows/*.yml files for module matrix completeness.

    For every matrix.module list found, verifies it contains exactly the set
    of known modules — reporting both omissions and stale entries.

    Returns the number of errors found.
    """
    workflows_dir = root / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return 0

    errors = 0
    # Diagnostics use the `path:line: message` shape parsed by the problem matcher in
    # .vscode/tasks.json. Missing entries anchor on the `module:` key line (no specific
    # offending line exists); stale entries anchor on the entry's own line.
    for wf_file in sorted(workflows_dir.glob("*.yml")):
        rel = str(wf_file.relative_to(root))
        for job_name, module_key_line, matrix_entries in workflow_module_lists(wf_file):
            matrix_set = set(matrix_entries)
            for mod in sorted(modules - matrix_set):
                print(f"{rel}:{module_key_line}: [{job_name}] missing matrix entry for ./{mod}")
                errors += 1
            for mod in sorted(matrix_set - modules):
                print(f"{rel}:{matrix_entries[mod]}: [{job_name}] stale matrix entry ./{mod} (no go.mod)")
                errors += 1

    return errors


def check_golangci_configs(root: Path, modules: set[Path]) -> int:
    """Check that every Go module has a .golangci.yml reachable via golangci-lint's
    config search (module directory or any parent up to the repo root).

    A root-level .golangci.yml is valid shared config — golangci-lint walks up
    the directory tree when no per-module config is present.

    Returns the number of errors found.
    """
    errors = 0
    for mod in sorted(modules):
        candidate = root / mod
        found = False
        while True:
            if (candidate / ".golangci.yml").is_file():
                found = True
                break
            if candidate == root:
                break
            candidate = candidate.parent
        if not found:
            # Anchor on the module's go.mod — there is no specific line "at fault" for a
            # missing config file, but go.mod is the file whose existence makes the check run.
            print(f"{mod}/go.mod:1: no .golangci.yml reachable from ./{mod} (module dir or any parent up to repo root)")
            errors += 1
    return errors


def main() -> int:
    root = workspace_root()
    modules = found_modules(root)

    errors = 0
    errors += check_workflow_matrices(root, modules)
    errors += check_golangci_configs(root, modules)

    if errors == 0:
        print("All Go modules are consistently tracked.")
    return errors


if __name__ == "__main__":
    sys.exit(main())
