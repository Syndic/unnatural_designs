"""Tests for check_modules.py.

Workflow-matrix parsing and Go discovery coverage carried over from the predecessor
(test_check_go_modules.py); Python invariants tested directly.
"""

import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from meta.scripts._workspace import (
    find_go_modules,
    find_python_projects,
    workflow_module_lists,
)
from meta.scripts.check_modules import (
    LANGUAGES,
    check_module_configs,
    check_python_workspace_members,
    check_python_workspace_root,
    check_uv_lock_fresh,
    check_workflow_matrices,
)

GO = next(lang for lang in LANGUAGES if lang.name == "go")
PY = next(lang for lang in LANGUAGES if lang.name == "python")


# ── Helpers ────────────────────────────────────────────────────────────────────


def make_go_module(root: Path, rel_path: str) -> None:
    mod_dir = root / rel_path
    mod_dir.mkdir(parents=True, exist_ok=True)
    (mod_dir / "go.mod").write_text(
        f"module github.com/Syndic/unnatural_designs/{rel_path}\ngo 1.26.1\n"
    )


def make_golangci(root: Path, rel_path: str) -> None:
    config = root / rel_path / ".golangci.yml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("linters:\n  enable:\n    - gosec\n")


def make_python_project(root: Path, rel_path: str) -> None:
    proj_dir = root / rel_path
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "pyproject.toml").write_text(
        f'[project]\nname = "{rel_path.replace("/", "-")}"\nversion = "0.0.0"\n'
    )


def make_workflow(root: Path, name: str, content: str) -> Path:
    wf_dir = root / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    path = wf_dir / name
    path.write_text(content)
    return path


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


def make_module_workflow(root: Path, name: str, jobs: dict[str, list[str]]) -> Path:
    job_blocks = "".join(
        _JOB_TEMPLATE.format(
            job_name=job_name,
            module_lines="\n".join(f"          - {m}" for m in modules),
        )
        for job_name, modules in jobs.items()
    )
    return make_workflow(root, name, _WORKFLOW_HEADER + job_blocks)


# ── TestFindGoModules ──────────────────────────────────────────────────────────


class TestFindGoModules(unittest.TestCase):
    def test_no_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(find_go_modules(Path(tmp)), set())

    def test_finds_single_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_go_module(root, "tools/foo")
            self.assertEqual(find_go_modules(root), {Path("tools/foo")})

    def test_finds_multiple_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_go_module(root, "tools/foo")
            make_go_module(root, "libs/bar")
            self.assertEqual(find_go_modules(root), {Path("tools/foo"), Path("libs/bar")})

    def test_excludes_bazel_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_go_module(root, "bazel-out/fake")
            self.assertEqual(find_go_modules(root), set())

    def test_excludes_git_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_go_module(root, ".git/fake")
            self.assertEqual(find_go_modules(root), set())


# ── TestFindPythonProjects ─────────────────────────────────────────────────────


class TestFindPythonProjects(unittest.TestCase):
    def test_no_projects(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(find_python_projects(Path(tmp)), set())

    def test_excludes_root_pyproject(self):
        """The workspace root pyproject is not a project; discovery skips it."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname = 'root'\n")
            self.assertEqual(find_python_projects(root), set())

    def test_finds_subdir_projects(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_python_project(root, "tools/foo")
            make_python_project(root, "libs/bar")
            self.assertEqual(find_python_projects(root), {Path("tools/foo"), Path("libs/bar")})

    def test_excludes_venv(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_python_project(root, ".venv/some-dep")
            self.assertEqual(find_python_projects(root), set())

    def test_excludes_bazel(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_python_project(root, "bazel-out/fake")
            self.assertEqual(find_python_projects(root), set())


# ── TestWorkflowModuleLists ────────────────────────────────────────────────────
# Carried over from test_check_go_modules.py with only the import path changed.


class TestWorkflowModuleLists(unittest.TestCase):
    def _parse(self, content: str) -> list[tuple[str, int, dict[Path, int]]]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "workflow.yml"
            path.write_text(content)
            return workflow_module_lists(path)

    def test_no_jobs(self):
        self.assertEqual(self._parse("name: CI\non:\n  push:\n    branches: [main]\n"), [])

    def test_single_job_single_module(self):
        content = textwrap.dedent("""\
            jobs:
              scan:
                strategy:
                  matrix:
                    module:
                      - tools/foo
                steps:
                  - run: echo hi
            """)
        result = self._parse(content)
        self.assertEqual(len(result), 1)
        job, module_key_line, modules = result[0]
        self.assertEqual(job, "scan")
        self.assertEqual(module_key_line, 5)
        self.assertEqual(modules, {Path("tools/foo"): 6})

    def test_two_jobs_both_with_matrices(self):
        content = textwrap.dedent("""\
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
            """)
        result = self._parse(content)
        self.assertEqual(len(result), 2)
        self.assertEqual({job for job, _, _ in result}, {"govulncheck", "golangci-lint"})

    def test_job_with_no_matrix_does_not_affect_next_job(self):
        content = textwrap.dedent("""\
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
            """)
        result = self._parse(content)
        self.assertEqual(len(result), 1)
        job, _, modules = result[0]
        self.assertEqual(job, "govulncheck")
        self.assertEqual(set(modules), {Path("tools/foo")})

    def test_comment_in_module_list_skipped(self):
        content = textwrap.dedent("""\
            jobs:
              scan:
                strategy:
                  matrix:
                    module:
                      # - tools/commented-out
                      - tools/foo
                steps:
                  - run: echo hi
            """)
        _, _, modules = self._parse(content)[0]
        self.assertEqual(set(modules), {Path("tools/foo")})


# ── TestCheckWorkflowMatrices ──────────────────────────────────────────────────


class TestCheckWorkflowMatrices(unittest.TestCase):
    def test_consistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_go_module(root, "tools/foo")
            make_module_workflow(root, "security.yml", {"scan": ["tools/foo"]})
            self.assertEqual(check_workflow_matrices(root, find_go_modules(root)), 0)

    def test_module_missing_from_matrix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_go_module(root, "tools/foo")
            make_go_module(root, "tools/bar")
            make_module_workflow(root, "security.yml", {"scan": ["tools/foo"]})
            self.assertEqual(check_workflow_matrices(root, find_go_modules(root)), 1)

    def test_stale_entry_in_matrix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_go_module(root, "tools/foo")
            make_module_workflow(root, "security.yml", {"scan": ["tools/foo", "tools/nonexistent"]})
            self.assertEqual(check_workflow_matrices(root, find_go_modules(root)), 1)

    def test_two_jobs_both_missing_same_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_go_module(root, "tools/foo")
            make_go_module(root, "tools/bar")
            make_module_workflow(
                root,
                "security.yml",
                {"govulncheck": ["tools/foo"], "golangci-lint": ["tools/foo"]},
            )
            self.assertEqual(check_workflow_matrices(root, find_go_modules(root)), 2)

    def test_no_workflows_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_go_module(root, "tools/foo")
            self.assertEqual(check_workflow_matrices(root, find_go_modules(root)), 0)


# ── TestCheckModuleConfigs ─────────────────────────────────────────────────────


class TestCheckModuleConfigsGo(unittest.TestCase):
    def test_per_module_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_go_module(root, "tools/foo")
            make_golangci(root, "tools/foo")
            self.assertEqual(check_module_configs(root, GO, find_go_modules(root)), 0)

    def test_missing_config_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_go_module(root, "tools/foo")
            self.assertEqual(check_module_configs(root, GO, find_go_modules(root)), 1)

    def test_root_config_satisfies_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_go_module(root, "tools/foo")
            (root / ".golangci.yml").write_text("linters: {}\n")
            self.assertEqual(check_module_configs(root, GO, find_go_modules(root)), 0)

    def test_directory_named_like_config_does_not_satisfy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_go_module(root, "tools/foo")
            (root / "tools/foo/.golangci.yml").mkdir(parents=True, exist_ok=True)
            self.assertEqual(check_module_configs(root, GO, find_go_modules(root)), 1)


class TestCheckModuleConfigsPython(unittest.TestCase):
    def test_per_project_pyproject_satisfies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_python_project(root, "tools/foo")
            self.assertEqual(check_module_configs(root, PY, find_python_projects(root)), 0)

    def test_no_projects_vacuously_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(check_module_configs(Path(tmp), PY, set()), 0)


# ── TestCheckPythonWorkspaceRoot ───────────────────────────────────────────────


def _write_pyproject(root: Path, body: str) -> None:
    (root / "pyproject.toml").write_text(body)


_FULL_PYPROJECT = textwrap.dedent("""\
    [project]
    name = "ws"
    version = "0.0.0"
    [tool.uv.workspace]
    members = []
    [tool.ruff]
    line-length = 100
    [tool.ty]
    """)


class TestCheckPythonWorkspaceRoot(unittest.TestCase):
    def test_all_sections_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_pyproject(root, _FULL_PYPROJECT)
            self.assertEqual(check_python_workspace_root(root), 0)

    def test_missing_uv_workspace_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_pyproject(root, "[tool.ruff]\n[tool.ty]\n")
            self.assertEqual(check_python_workspace_root(root), 1)

    def test_missing_ruff_and_ty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_pyproject(root, "[tool.uv.workspace]\nmembers = []\n")
            self.assertEqual(check_python_workspace_root(root), 2)

    def test_missing_pyproject_entirely(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(check_python_workspace_root(Path(tmp)), 1)

    def test_malformed_pyproject(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_pyproject(root, "this is not valid TOML at all\n[unclosed")
            self.assertEqual(check_python_workspace_root(root), 1)


# ── TestCheckPythonWorkspaceMembers ────────────────────────────────────────────


class TestCheckPythonWorkspaceMembers(unittest.TestCase):
    def test_project_listed_by_exact_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_pyproject(
                root,
                textwrap.dedent("""\
                    [tool.uv.workspace]
                    members = ["tools/foo"]
                    """),
            )
            make_python_project(root, "tools/foo")
            self.assertEqual(check_python_workspace_members(root, find_python_projects(root)), 0)

    def test_project_listed_by_glob(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_pyproject(
                root,
                textwrap.dedent("""\
                    [tool.uv.workspace]
                    members = ["tools/*"]
                    """),
            )
            make_python_project(root, "tools/foo")
            make_python_project(root, "tools/bar")
            self.assertEqual(check_python_workspace_members(root, find_python_projects(root)), 0)

    def test_project_not_listed_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_pyproject(
                root,
                textwrap.dedent("""\
                    [tool.uv.workspace]
                    members = ["tools/*"]
                    """),
            )
            make_python_project(root, "libs/orphan")
            self.assertEqual(check_python_workspace_members(root, find_python_projects(root)), 1)

    def test_no_projects_vacuously_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_pyproject(root, "[tool.uv.workspace]\nmembers = []\n")
            self.assertEqual(check_python_workspace_members(root, set()), 0)


# ── TestCheckUvLockFresh ───────────────────────────────────────────────────────


class TestCheckUvLockFresh(unittest.TestCase):
    def test_uv_not_available_silently_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "requirements_lock.txt").write_text("# stub\n")
            with mock.patch("meta.scripts.check_modules._uv_export", return_value=None):
                self.assertEqual(check_uv_lock_fresh(root), 0)

    def test_missing_requirements_lock_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(check_uv_lock_fresh(Path(tmp)), 1)

    def test_export_matches_checked_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "requirements_lock.txt").write_text("foo==1.0\n")
            with mock.patch("meta.scripts.check_modules._uv_export", return_value="foo==1.0\n"):
                self.assertEqual(check_uv_lock_fresh(root), 0)

    def test_export_diff_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "requirements_lock.txt").write_text("foo==1.0\n")
            with mock.patch("meta.scripts.check_modules._uv_export", return_value="foo==2.0\n"):
                self.assertEqual(check_uv_lock_fresh(root), 1)


if __name__ == "__main__":
    unittest.main()
