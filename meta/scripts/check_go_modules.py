#!/usr/bin/env python3
"""
Verifies that all Go modules in the repo are consistently tracked:

  1. Every matrix.module list in .github/workflows/*.yml contains exactly
     the set of Go modules known from go.mod discovery (no missing, no stale).
  2. Every Go module has a .golangci.yml configuration file.

Usage: ./meta/scripts/check_go_modules.py
"""

import subprocess
import sys
from pathlib import Path


def workspace_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    )
    return Path(result.stdout.strip())


def found_modules(root: Path) -> set[Path]:
    """Find all Go modules in the repo, excluding Bazel output symlinks and .git."""
    return {
        p.parent.relative_to(root)
        for p in root.rglob("go.mod")
        if not any(part.startswith("bazel-") or part == ".git" for part in p.parts)
    }


def workflow_module_lists(workflow_file: Path) -> list[tuple[str, frozenset[Path]]]:
    """Parse a GitHub Actions workflow file and return all matrix.module lists.

    Returns one (job_name, module_set) pair per matrix.module block found.
    job_name is the YAML job key, captured on a best-effort basis by tracking keys at indent 2
    inside the jobs: block; falls back to '<unknown>'.

    Parsing is line-oriented and indent-aware, with no third-party dependencies.
    The GitHub Actions YAML structure is regular enough to make this reliable:

        jobs:
          <job-key>:           # indent 2  — recorded as job_name
            strategy:
              matrix:          # marks start of matrix block
                module:        # marks start of module list
                  - some/path  # collected as a module entry

    The parser uses two guard checks that fire before each line is processed, handling dedent out
    of the module list and out of the matrix block. This avoids re-processing lines across state
    transitions.
    """
    text = workflow_file.read_text()
    result: list[tuple[str, frozenset[Path]]] = []

    state = "scanning"   # scanning | in_matrix | in_module
    matrix_indent = -1
    module_indent = -1
    current: list[Path] | None = None

    # Job name tracking: record the YAML key at indent 2 inside jobs:.
    in_jobs = False
    current_job = "<unknown>"

    for raw in text.splitlines():
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
                result.append((current_job, frozenset(current)))
            current = None
            module_indent = -1
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
                current = []

        elif state == "in_module":
            if stripped.startswith("- "):
                current.append(Path(stripped[2:].strip()))

    # End of file while still inside a module list.
    if state == "in_module" and current is not None:
        result.append((current_job, frozenset(current)))

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
    for wf_file in sorted(workflows_dir.glob("*.yml")):
        rel = str(wf_file.relative_to(root))
        for job_name, matrix in workflow_module_lists(wf_file):
            label = f"{rel} [{job_name}]"
            for mod in sorted(modules - matrix):
                print(f"MISSING from {label}: ./{mod} has a go.mod but no matrix entry")
                errors += 1
            for mod in sorted(matrix - modules):
                print(f"STALE in {label}: ./{mod} has a matrix entry but no go.mod")
                errors += 1

    return errors


def check_golangci_configs(root: Path, modules: set[Path]) -> int:
    """Check that every Go module has a .golangci.yml configuration file.

    Returns the number of errors found.
    """
    errors = 0
    for mod in sorted(modules):
        config = root / mod / ".golangci.yml"
        if not config.is_file():
            print(f"MISSING .golangci.yml in: ./{mod}")
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
