"""Tests for check_go_modules.py."""

import tempfile
import unittest
from pathlib import Path

from meta.scripts.check_go_modules import (
    check_golangci_configs,
    check_workflow_matrices,
    found_modules,
    workflow_module_lists,
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def make_module(root: Path, rel_path: str) -> None:
    mod_dir = root / rel_path
    mod_dir.mkdir(parents=True, exist_ok=True)
    (mod_dir / "go.mod").write_text(
        f"module github.com/Syndic/unnatural_designs/{rel_path}\ngo 1.26.1\n"
    )


def make_golangci(root: Path, rel_path: str) -> None:
    config = root / rel_path / ".golangci.yml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("linters:\n  enable:\n    - gosec\n")


def make_workflow(root: Path, name: str, content: str) -> Path:
    wf_dir = root / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    path = wf_dir / name
    path.write_text(content)
    return path


# Mirrors the real security.yml structure: 2-space indent throughout,
# matrix.module list items at 10 spaces.
_JOB_TEMPLATE = """\
  {job_name}:
    name: {job_name} (${{{{ matrix.module }}}})
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        module:
{module_lines}
    steps:
      - run: echo hi
"""

_WORKFLOW_HEADER = """\
name: Security
on:
  push:
    branches: [main]
jobs:
"""


def make_module_workflow(
    root: Path, name: str, jobs: dict[str, list[str]]
) -> Path:
    """Create a workflow file with one job per entry in jobs, each with a module matrix."""
    job_blocks = "".join(
        _JOB_TEMPLATE.format(
            job_name=job_name,
            module_lines="\n".join(f"          - {m}" for m in modules),
        )
        for job_name, modules in jobs.items()
    )
    return make_workflow(root, name, _WORKFLOW_HEADER + job_blocks)


# ── TestFoundModules ───────────────────────────────────────────────────────────


class TestFoundModules(unittest.TestCase):

    def test_no_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(found_modules(Path(tmp)), set())

    def test_finds_single_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_module(root, "tools/foo")
            self.assertEqual(found_modules(root), {Path("tools/foo")})

    def test_finds_multiple_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_module(root, "tools/foo")
            make_module(root, "libs/bar")
            self.assertEqual(found_modules(root), {Path("tools/foo"), Path("libs/bar")})

    def test_excludes_bazel_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_module(root, "bazel-out/fake")
            self.assertEqual(found_modules(root), set())

    def test_excludes_git_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_module(root, ".git/fake")
            self.assertEqual(found_modules(root), set())


# ── TestWorkflowModuleLists ────────────────────────────────────────────────────


class TestWorkflowModuleLists(unittest.TestCase):

    def _parse(self, content: str) -> list[tuple[str, frozenset[Path]]]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "workflow.yml"
            path.write_text(content)
            return workflow_module_lists(path)

    def test_no_jobs(self):
        self.assertEqual(self._parse("name: CI\non:\n  push:\n    branches: [main]\n"), [])

    def test_job_with_no_matrix(self):
        content = """\
name: CI
on:
  push:
    branches: [main]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
"""
        self.assertEqual(self._parse(content), [])

    def test_matrix_without_module_key(self):
        content = """\
jobs:
  build:
    strategy:
      matrix:
        os: [ubuntu-latest, macos-latest]
    steps:
      - run: echo hi
"""
        self.assertEqual(self._parse(content), [])

    def test_single_job_single_module(self):
        content = """\
jobs:
  scan:
    strategy:
      matrix:
        module:
          - tools/foo
    steps:
      - run: echo hi
"""
        result = self._parse(content)
        self.assertEqual(len(result), 1)
        job, modules = result[0]
        self.assertEqual(job, "scan")
        self.assertEqual(modules, frozenset([Path("tools/foo")]))

    def test_single_job_multiple_modules(self):
        content = """\
jobs:
  scan:
    strategy:
      matrix:
        module:
          - tools/foo
          - libs/bar
    steps:
      - run: echo hi
"""
        result = self._parse(content)
        self.assertEqual(len(result), 1)
        _, modules = result[0]
        self.assertEqual(modules, frozenset([Path("tools/foo"), Path("libs/bar")]))

    def test_two_jobs_both_with_module_matrices(self):
        content = """\
jobs:
  govulncheck:
    strategy:
      matrix:
        module:
          - tools/foo
    steps:
      - run: echo hi
  golangci-lint:
    strategy:
      matrix:
        module:
          - tools/foo
    steps:
      - run: echo hi
"""
        result = self._parse(content)
        self.assertEqual(len(result), 2)
        jobs = {job for job, _ in result}
        self.assertEqual(jobs, {"govulncheck", "golangci-lint"})

    def test_two_jobs_only_one_has_module_matrix(self):
        content = """\
jobs:
  build:
    strategy:
      matrix:
        os: [ubuntu-latest]
    steps:
      - run: echo hi
  lint:
    strategy:
      matrix:
        module:
          - tools/foo
    steps:
      - run: echo hi
"""
        result = self._parse(content)
        self.assertEqual(len(result), 1)
        job, _ = result[0]
        self.assertEqual(job, "lint")

    def test_job_name_with_hyphens(self):
        content = """\
jobs:
  golangci-lint:
    strategy:
      matrix:
        module:
          - tools/foo
    steps:
      - run: echo hi
"""
        result = self._parse(content)
        self.assertEqual(len(result), 1)
        job, _ = result[0]
        self.assertEqual(job, "golangci-lint")

    def test_comment_lines_skipped(self):
        content = """\
jobs:
  scan:
    strategy:
      matrix:
        module:
          # - tools/commented-out
          - tools/foo
    steps:
      - run: echo hi
"""
        result = self._parse(content)
        self.assertEqual(len(result), 1)
        _, modules = result[0]
        self.assertEqual(modules, frozenset([Path("tools/foo")]))

    def test_comment_job_block_does_not_confuse_job_name(self):
        """Comment lines at indent 2 must not be mistaken for job names."""
        content = """\
jobs:
  # ── Section header ─────────────────────────────────────────────────────────
  real-job:
    strategy:
      matrix:
        module:
          - tools/foo
    steps:
      - run: echo hi
"""
        result = self._parse(content)
        self.assertEqual(len(result), 1)
        job, _ = result[0]
        self.assertEqual(job, "real-job")

    def test_job_with_no_matrix_does_not_affect_next_job(self):
        """A job without a matrix must not suppress detection in subsequent jobs."""
        content = """\
jobs:
  semgrep:
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
  govulncheck:
    strategy:
      matrix:
        module:
          - tools/foo
    steps:
      - run: echo hi
"""
        result = self._parse(content)
        self.assertEqual(len(result), 1)
        job, modules = result[0]
        self.assertEqual(job, "govulncheck")
        self.assertEqual(modules, frozenset([Path("tools/foo")]))

    def test_empty_workflow(self):
        self.assertEqual(self._parse(""), [])

    def test_real_security_yml_shape(self):
        """Validate against a realistic excerpt matching the actual security.yml layout."""
        content = """\
name: Security

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  # ── Semgrep ─────────────────────────────────────────────────────────────────
  semgrep:
    name: Semgrep
    runs-on: ubuntu-latest
    permissions:
      security-events: write
      contents: read
    steps:
      - uses: actions/checkout@v6

  # ── govulncheck ──────────────────────────────────────────────────────────────
  govulncheck:
    name: govulncheck (${{ matrix.module }})
    runs-on: ubuntu-latest
    permissions:
      security-events: write
      contents: read
    strategy:
      fail-fast: false
      matrix:
        module:
          - tools/network_infrastructure_maintenance
    steps:
      - uses: actions/checkout@v6

  # ── golangci-lint ────────────────────────────────────────────────────────────
  golangci-lint:
    name: golangci-lint (${{ matrix.module }})
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        module:
          - tools/network_infrastructure_maintenance
    steps:
      - uses: actions/checkout@v6

  # ── Trivy ────────────────────────────────────────────────────────────────────
  trivy:
    name: Trivy
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
"""
        result = self._parse(content)
        # semgrep and trivy have no module matrix; govulncheck and golangci-lint do
        self.assertEqual(len(result), 2)
        by_job = {job: mods for job, mods in result}
        self.assertIn("govulncheck", by_job)
        self.assertIn("golangci-lint", by_job)
        self.assertNotIn("semgrep", by_job)
        self.assertNotIn("trivy", by_job)
        expected = frozenset([Path("tools/network_infrastructure_maintenance")])
        self.assertEqual(by_job["govulncheck"], expected)
        self.assertEqual(by_job["golangci-lint"], expected)


# ── TestCheckWorkflowMatrices ──────────────────────────────────────────────────


class TestCheckWorkflowMatrices(unittest.TestCase):

    def test_consistent_single_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_module(root, "tools/foo")
            make_module_workflow(root, "security.yml", {"scan": ["tools/foo"]})
            self.assertEqual(check_workflow_matrices(root, found_modules(root)), 0)

    def test_consistent_multiple_jobs_same_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_module(root, "tools/foo")
            make_module(root, "libs/bar")
            make_module_workflow(root, "security.yml", {
                "govulncheck": ["tools/foo", "libs/bar"],
                "golangci-lint": ["tools/foo", "libs/bar"],
            })
            self.assertEqual(check_workflow_matrices(root, found_modules(root)), 0)

    def test_module_missing_from_matrix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_module(root, "tools/foo")
            make_module(root, "tools/bar")
            make_module_workflow(root, "security.yml", {"scan": ["tools/foo"]})
            self.assertEqual(check_workflow_matrices(root, found_modules(root)), 1)

    def test_stale_entry_in_matrix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_module(root, "tools/foo")
            make_module_workflow(root, "security.yml", {
                "scan": ["tools/foo", "tools/nonexistent"],
            })
            self.assertEqual(check_workflow_matrices(root, found_modules(root)), 1)

    def test_missing_and_stale_are_both_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_module(root, "tools/new")
            make_module_workflow(root, "security.yml", {"scan": ["tools/old"]})
            self.assertEqual(check_workflow_matrices(root, found_modules(root)), 2)

    def test_two_jobs_both_missing_same_module(self):
        """A module missing from two matrix lists counts as two errors."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_module(root, "tools/foo")
            make_module(root, "tools/bar")
            make_module_workflow(root, "security.yml", {
                "govulncheck": ["tools/foo"],
                "golangci-lint": ["tools/foo"],
            })
            # tools/bar missing from both matrices = 2 errors
            self.assertEqual(check_workflow_matrices(root, found_modules(root)), 2)

    def test_workflow_with_no_module_matrix_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_module(root, "tools/foo")
            make_workflow(root, "ci.yml", "name: CI\non:\n  push:\n    branches: [main]\n")
            self.assertEqual(check_workflow_matrices(root, found_modules(root)), 0)

    def test_multiple_workflow_files_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_module(root, "tools/foo")
            make_module_workflow(root, "security.yml", {"scan": ["tools/foo"]})
            make_module_workflow(root, "other.yml", {"scan": ["tools/foo"]})
            self.assertEqual(check_workflow_matrices(root, found_modules(root)), 0)

    def test_no_workflows_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_module(root, "tools/foo")
            # No .github/workflows directory
            self.assertEqual(check_workflow_matrices(root, found_modules(root)), 0)

    def test_no_modules_no_matrices(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workflow(root, "ci.yml", "name: CI\non:\n  push:\n    branches: [main]\n")
            self.assertEqual(check_workflow_matrices(root, set()), 0)


# ── TestCheckGolangciConfigs ───────────────────────────────────────────────────


class TestCheckGolangciConfigs(unittest.TestCase):

    def test_all_modules_have_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_module(root, "tools/foo")
            make_golangci(root, "tools/foo")
            self.assertEqual(check_golangci_configs(root, found_modules(root)), 0)

    def test_missing_config_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_module(root, "tools/foo")
            # No .golangci.yml
            self.assertEqual(check_golangci_configs(root, found_modules(root)), 1)

    def test_some_present_some_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_module(root, "tools/foo")
            make_golangci(root, "tools/foo")
            make_module(root, "libs/bar")
            # No .golangci.yml for libs/bar
            self.assertEqual(check_golangci_configs(root, found_modules(root)), 1)

    def test_multiple_modules_all_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_module(root, "tools/foo")
            make_module(root, "libs/bar")
            self.assertEqual(check_golangci_configs(root, found_modules(root)), 2)

    def test_no_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(check_golangci_configs(Path(tmp), set()), 0)

    def test_directory_named_golangci_yml_does_not_satisfy_check(self):
        """A directory named .golangci.yml must not satisfy the file presence check."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_module(root, "tools/foo")
            # Create a directory instead of a file
            (root / "tools/foo/.golangci.yml").mkdir(parents=True, exist_ok=True)
            self.assertEqual(check_golangci_configs(root, found_modules(root)), 1)

    def test_root_config_satisfies_module_without_own_config(self):
        """A root-level .golangci.yml is accepted for a module that has no per-module config."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_module(root, "tools/foo")
            (root / ".golangci.yml").write_text("linters:\n  enable:\n    - gosec\n")
            self.assertEqual(check_golangci_configs(root, found_modules(root)), 0)

    def test_root_config_satisfies_multiple_modules(self):
        """A single root-level .golangci.yml covers all modules in the repo."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_module(root, "tools/foo")
            make_module(root, "libs/bar")
            (root / ".golangci.yml").write_text("linters:\n  enable:\n    - gosec\n")
            self.assertEqual(check_golangci_configs(root, found_modules(root)), 0)

    def test_intermediate_directory_config_satisfies_module(self):
        """A .golangci.yml anywhere between the module dir and the repo root is accepted."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_module(root, "tools/foo")
            (root / "tools").mkdir(parents=True, exist_ok=True)
            (root / "tools" / ".golangci.yml").write_text("linters:\n  enable:\n    - gosec\n")
            self.assertEqual(check_golangci_configs(root, found_modules(root)), 0)

    def test_root_directory_named_golangci_yml_does_not_satisfy(self):
        """A directory named .golangci.yml at the root must not satisfy the check."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_module(root, "tools/foo")
            (root / ".golangci.yml").mkdir(parents=True, exist_ok=True)
            self.assertEqual(check_golangci_configs(root, found_modules(root)), 1)


if __name__ == "__main__":
    unittest.main()
