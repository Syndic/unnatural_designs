"""Tests for check_modules.py.

Workflow-matrix parsing and Go discovery coverage carried over from the predecessor
(test_check_go_modules.py); Python invariants tested directly.
"""

import io
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from meta.scripts import check_modules
from meta.scripts._workspace import (
    find_go_modules,
    find_python_projects,
    workflow_matrix_lists,
)
from meta.scripts.check_modules import (
    LANGUAGES,
    _strip_header,
    _uv_export,
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
    name: {job_name} (${{{{ matrix.go_module }}}})
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        go_module:
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
    def _parse(
        self, content: str, matrix_key: str = "go_module"
    ) -> list[tuple[str, int, dict[Path, int]]]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "workflow.yml"
            path.write_text(content)
            return workflow_matrix_lists(path, matrix_key)

    def test_no_jobs(self):
        self.assertEqual(self._parse("name: CI\non:\n  push:\n    branches: [main]\n"), [])

    def test_single_job_single_module(self):
        content = textwrap.dedent("""\
            jobs:
              scan:
                strategy:
                  matrix:
                    go_module:
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
                    go_module:
                      - tools/foo
                steps:
                  - run: echo hi
              golangci-lint:
                strategy:
                  matrix:
                    go_module:
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
                    go_module:
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
                    go_module:
                      # - tools/commented-out
                      - tools/foo
                steps:
                  - run: echo hi
            """)
        _, _, modules = self._parse(content)[0]
        self.assertEqual(set(modules), {Path("tools/foo")})

    def test_empty_workflow(self):
        self.assertEqual(self._parse(""), [])

    def test_job_with_no_matrix(self):
        content = textwrap.dedent("""\
            name: CI
            on:
              push:
                branches: [main]
            jobs:
              build:
                runs-on: ubuntu-latest
                steps:
                  - run: echo hi
            """)
        self.assertEqual(self._parse(content), [])

    def test_matrix_without_module_key(self):
        """A matrix block keyed on something other than the target key is ignored."""
        content = textwrap.dedent("""\
            jobs:
              build:
                strategy:
                  matrix:
                    os: [ubuntu-latest, macos-latest]
                steps:
                  - run: echo hi
            """)
        self.assertEqual(self._parse(content), [])

    def test_single_job_multiple_modules(self):
        content = textwrap.dedent("""\
            jobs:
              scan:
                strategy:
                  matrix:
                    go_module:
                      - tools/foo
                      - libs/bar
                steps:
                  - run: echo hi
            """)
        result = self._parse(content)
        self.assertEqual(len(result), 1)
        _, _, modules = result[0]
        self.assertEqual(set(modules), {Path("tools/foo"), Path("libs/bar")})

    def test_two_jobs_only_one_has_module_matrix(self):
        """A matrix without the target key in one job must not bleed into the next job's parse."""
        content = textwrap.dedent("""\
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
                    go_module:
                      - tools/foo
                steps:
                  - run: echo hi
            """)
        result = self._parse(content)
        self.assertEqual(len(result), 1)
        job, _, _ = result[0]
        self.assertEqual(job, "lint")

    def test_job_name_with_hyphens(self):
        content = textwrap.dedent("""\
            jobs:
              golangci-lint:
                strategy:
                  matrix:
                    go_module:
                      - tools/foo
                steps:
                  - run: echo hi
            """)
        result = self._parse(content)
        self.assertEqual(len(result), 1)
        job, _, _ = result[0]
        self.assertEqual(job, "golangci-lint")

    def test_comment_job_block_does_not_confuse_job_name(self):
        """A `# ── Section ──` comment at indent 2 must not be mistaken for a job key."""
        content = textwrap.dedent("""\
            jobs:
              # ── Section header ─────────────────────────────────────────────────────────
              real-job:
                strategy:
                  matrix:
                    go_module:
                      - tools/foo
                steps:
                  - run: echo hi
            """)
        result = self._parse(content)
        self.assertEqual(len(result), 1)
        job, _, _ = result[0]
        self.assertEqual(job, "real-job")

    def test_real_security_yml_shape(self):
        """Realistic security.yml excerpt — mix of matrixed and non-matrixed jobs."""
        content = textwrap.dedent("""\
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
                name: govulncheck (${{ matrix.go_module }})
                runs-on: ubuntu-latest
                permissions:
                  security-events: write
                  contents: read
                strategy:
                  fail-fast: false
                  matrix:
                    go_module:
                      - tools/network_infrastructure_maintenance
                steps:
                  - uses: actions/checkout@v6

              # ── golangci-lint ────────────────────────────────────────────────────────────
              golangci-lint:
                name: golangci-lint (${{ matrix.go_module }})
                runs-on: ubuntu-latest
                strategy:
                  fail-fast: false
                  matrix:
                    go_module:
                      - tools/network_infrastructure_maintenance
                steps:
                  - uses: actions/checkout@v6

              # ── Trivy ────────────────────────────────────────────────────────────────────
              trivy:
                name: Trivy
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@v6
            """)
        result = self._parse(content)
        self.assertEqual(len(result), 2)
        by_job = {job: set(mods) for job, _, mods in result}
        self.assertIn("govulncheck", by_job)
        self.assertIn("golangci-lint", by_job)
        self.assertNotIn("semgrep", by_job)
        self.assertNotIn("trivy", by_job)
        expected = {Path("tools/network_infrastructure_maintenance")}
        self.assertEqual(by_job["govulncheck"], expected)
        self.assertEqual(by_job["golangci-lint"], expected)


# ── TestCheckWorkflowMatrices ──────────────────────────────────────────────────


class TestCheckWorkflowMatrices(unittest.TestCase):
    def test_consistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_go_module(root, "tools/foo")
            make_module_workflow(root, "security.yml", {"scan": ["tools/foo"]})
            self.assertEqual(check_workflow_matrices(root, find_go_modules(root), "go_module"), 0)

    def test_module_missing_from_matrix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_go_module(root, "tools/foo")
            make_go_module(root, "tools/bar")
            make_module_workflow(root, "security.yml", {"scan": ["tools/foo"]})
            self.assertEqual(check_workflow_matrices(root, find_go_modules(root), "go_module"), 1)

    def test_stale_entry_in_matrix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_go_module(root, "tools/foo")
            make_module_workflow(root, "security.yml", {"scan": ["tools/foo", "tools/nonexistent"]})
            self.assertEqual(check_workflow_matrices(root, find_go_modules(root), "go_module"), 1)

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
            self.assertEqual(check_workflow_matrices(root, find_go_modules(root), "go_module"), 2)

    def test_no_workflows_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_go_module(root, "tools/foo")
            self.assertEqual(check_workflow_matrices(root, find_go_modules(root), "go_module"), 0)

    def test_consistent_multiple_jobs_same_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_go_module(root, "tools/foo")
            make_go_module(root, "libs/bar")
            make_module_workflow(
                root,
                "security.yml",
                {
                    "govulncheck": ["tools/foo", "libs/bar"],
                    "golangci-lint": ["tools/foo", "libs/bar"],
                },
            )
            self.assertEqual(check_workflow_matrices(root, find_go_modules(root), "go_module"), 0)

    def test_missing_and_stale_are_both_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_go_module(root, "tools/new")
            make_module_workflow(root, "security.yml", {"scan": ["tools/old"]})
            self.assertEqual(check_workflow_matrices(root, find_go_modules(root), "go_module"), 2)

    def test_workflow_with_no_module_matrix_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_go_module(root, "tools/foo")
            make_workflow(root, "ci.yml", "name: CI\non:\n  push:\n    branches: [main]\n")
            self.assertEqual(check_workflow_matrices(root, find_go_modules(root), "go_module"), 0)

    def test_multiple_workflow_files_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_go_module(root, "tools/foo")
            make_module_workflow(root, "security.yml", {"scan": ["tools/foo"]})
            make_module_workflow(root, "other.yml", {"scan": ["tools/foo"]})
            self.assertEqual(check_workflow_matrices(root, find_go_modules(root), "go_module"), 0)

    def test_no_modules_no_matrices(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workflow(root, "ci.yml", "name: CI\non:\n  push:\n    branches: [main]\n")
            self.assertEqual(check_workflow_matrices(root, set(), "go_module"), 0)


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

    def test_some_present_some_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_go_module(root, "tools/foo")
            make_golangci(root, "tools/foo")
            make_go_module(root, "libs/bar")
            self.assertEqual(check_module_configs(root, GO, find_go_modules(root)), 1)

    def test_multiple_modules_all_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_go_module(root, "tools/foo")
            make_go_module(root, "libs/bar")
            self.assertEqual(check_module_configs(root, GO, find_go_modules(root)), 2)

    def test_intermediate_directory_config_satisfies_module(self):
        """A .golangci.yml anywhere between the module dir and the repo root is accepted."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_go_module(root, "tools/foo")
            (root / "tools").mkdir(parents=True, exist_ok=True)
            (root / "tools" / ".golangci.yml").write_text("linters: {}\n")
            self.assertEqual(check_module_configs(root, GO, find_go_modules(root)), 0)

    def test_root_config_satisfies_multiple_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_go_module(root, "tools/foo")
            make_go_module(root, "libs/bar")
            (root / ".golangci.yml").write_text("linters: {}\n")
            self.assertEqual(check_module_configs(root, GO, find_go_modules(root)), 0)

    def test_root_directory_named_config_does_not_satisfy(self):
        """A directory named .golangci.yml at the root must not satisfy the check."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_go_module(root, "tools/foo")
            (root / ".golangci.yml").mkdir(parents=True, exist_ok=True)
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

    def test_uv_export_failure_reported(self):
        """uv invocation itself fails (malformed pyproject, resolver error, etc.)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "requirements_lock.txt").write_text("foo==1.0\n")
            err = subprocess.CalledProcessError(
                returncode=1, cmd=["uv", "export"], stderr="resolution failed"
            )
            with mock.patch("meta.scripts.check_modules._uv_export", side_effect=err):
                with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                    rc = check_uv_lock_fresh(root)
                self.assertEqual(rc, 1)
                self.assertIn("uv export` failed", stdout.getvalue())


# ── TestStripHeader ────────────────────────────────────────────────────────────
# Internal helper, but load-bearing for check_uv_lock_fresh: the `# via X` annotations
# uv emits on each package line are leading-whitespace-prefixed and MUST survive stripping.
# A refactor that drops all `#`-prefixed lines would silently make lock drift undetectable.


class TestStripHeader(unittest.TestCase):
    def test_two_line_header_stripped(self):
        text = (
            "# This file was autogenerated by uv\n"
            "#    uv export --format requirements-txt\n"
            "certifi==2026.5.20\n"
        )
        self.assertEqual(_strip_header(text), "certifi==2026.5.20\n")

    def test_indented_via_annotation_preserved(self):
        """Per-package `# via X` lines have leading whitespace and must not be stripped."""
        text = (
            "# header line\n"
            "certifi==2026.5.20\n"
            "    # via requests\n"
            "requests==2.34.2\n"
            "    # via unnatural-designs-workspace\n"
        )
        expected = (
            "certifi==2026.5.20\n"
            "    # via requests\n"
            "requests==2.34.2\n"
            "    # via unnatural-designs-workspace\n"
        )
        self.assertEqual(_strip_header(text), expected)

    def test_no_header_passthrough(self):
        text = "certifi==2026.5.20\nrequests==2.34.2\n"
        self.assertEqual(_strip_header(text), text)

    def test_empty_input(self):
        self.assertEqual(_strip_header(""), "")

    def test_all_header_lines(self):
        text = "# one\n# two\n# three\n"
        self.assertEqual(_strip_header(text), "")

    def test_only_first_run_of_headers_stripped(self):
        """A `#`-prefixed line after content is not a header — only the leading run is dropped."""
        text = "# header\ncertifi==1.0\n# stray top-level comment mid-file\nfoo==2.0\n"
        expected = "certifi==1.0\n# stray top-level comment mid-file\nfoo==2.0\n"
        self.assertEqual(_strip_header(text), expected)


# ── TestCheckPythonWorkspaceMembersDefensive ───────────────────────────────────


class TestCheckPythonWorkspaceMembersDefensive(unittest.TestCase):
    def test_members_not_a_list_silently_passes(self):
        """Malformed config (members as a string) is silently a no-op — the workspace-root
        check is the canonical home for shape diagnostics, this check assumes well-formed input."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_pyproject(
                root,
                textwrap.dedent("""\
                    [tool.uv.workspace]
                    members = "not a list"
                    """),
            )
            make_python_project(root, "tools/foo")
            self.assertEqual(check_python_workspace_members(root, find_python_projects(root)), 0)


# ── TestUvExport ───────────────────────────────────────────────────────────────
# Direct-seam coverage for the argv contract. check_uv_lock_fresh tests mock at the
# _uv_export boundary, so they can't catch a refactor that silently swaps a flag
# (e.g. --no-emit-project -> --no-emit-workspace, or --format requirements-txt ->
# --format json). The freshness check's correctness depends on these flags mirroring
# the uv-lock-fresh pre-commit hook's invocation; this is the test that catches drift.


class TestUvExport(unittest.TestCase):
    def test_invokes_uv_with_expected_flags(self):
        captured: list[list[str]] = []

        def fake_run(cmd, **_kwargs):
            captured.append(list(cmd))
            Path(cmd[cmd.index("--output-file") + 1]).write_text("ok\n")
            return mock.Mock(returncode=0)

        with mock.patch("subprocess.run", side_effect=fake_run):
            _uv_export(Path("/fake"))

        self.assertEqual(len(captured), 1)
        cmd = captured[0]
        self.assertEqual(cmd[:2], ["uv", "export"])
        self.assertIn("--no-hashes", cmd)
        self.assertIn("--no-emit-project", cmd)
        self.assertEqual(cmd[cmd.index("--format") + 1], "requirements-txt")


# ── TestMain ───────────────────────────────────────────────────────────────────
# Driver-level wiring. Mocks each per-language and per-invariant check function to
# return preset error counts; asserts main aggregates correctly and prints the
# success message only when every check returns 0. Catches "added a check but
# forgot to wire it into main" regressions.


class TestMain(unittest.TestCase):
    def _run(self, **return_values: int) -> tuple[int, str]:
        """Run check_modules.main() with mocks for every check function.

        return_values keys: 'configs_go', 'configs_python', 'matrices', 'py_root',
        'py_members', 'uv_lock'. Missing keys default to 0.
        """
        configs_results = {
            "go": return_values.get("configs_go", 0),
            "python": return_values.get("configs_python", 0),
        }

        def fake_configs(_root, language, _modules):
            return configs_results[language.name]

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(check_modules, "workspace_root", return_value=Path(tmp)),
            mock.patch.object(check_modules, "check_module_configs", side_effect=fake_configs),
            mock.patch.object(
                check_modules,
                "check_workflow_matrices",
                return_value=return_values.get("matrices", 0),
            ),
            mock.patch.object(
                check_modules,
                "check_python_workspace_root",
                return_value=return_values.get("py_root", 0),
            ),
            mock.patch.object(
                check_modules,
                "check_python_workspace_members",
                return_value=return_values.get("py_members", 0),
            ),
            mock.patch.object(
                check_modules,
                "check_uv_lock_fresh",
                return_value=return_values.get("uv_lock", 0),
            ),
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            rc = check_modules.main()
        return rc, stdout.getvalue()

    def test_all_clean_prints_success(self):
        rc, out = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("consistent", out)

    def test_aggregates_error_counts(self):
        rc, _ = self._run(
            configs_go=2, configs_python=1, matrices=3, py_root=1, py_members=2, uv_lock=1
        )
        self.assertEqual(rc, 10)

    def test_success_message_suppressed_on_any_error(self):
        rc, out = self._run(uv_lock=1)
        self.assertEqual(rc, 1)
        self.assertNotIn("consistent", out)

    def test_workflow_matrix_check_runs_for_go_only(self):
        """The matrix-completeness check runs once per language whose LanguageSpec has a
        matrix_key. Today only Go does (`go_module`); Python's matrix_key=None must skip the
        check so Go entries aren't compared against the Python discovered-set."""
        matrix_calls: list[str] = []

        def fake_matrices(_root, _modules, _matrix_key):
            matrix_calls.append("called")
            return 0

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(check_modules, "workspace_root", return_value=Path(tmp)),
            mock.patch.object(check_modules, "check_module_configs", return_value=0),
            mock.patch.object(check_modules, "check_workflow_matrices", side_effect=fake_matrices),
            mock.patch.object(check_modules, "check_python_workspace_root", return_value=0),
            mock.patch.object(check_modules, "check_python_workspace_members", return_value=0),
            mock.patch.object(check_modules, "check_uv_lock_fresh", return_value=0),
            mock.patch("sys.stdout", new_callable=io.StringIO),
        ):
            check_modules.main()
        # Exactly one call — the Go-branch in the LANGUAGES loop, no Python double-run.
        self.assertEqual(len(matrix_calls), 1)


if __name__ == "__main__":
    unittest.main()
