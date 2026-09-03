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
# --format json). The freshness check's correctness depends on these flags matching the
# uv-lock-fresh pre-commit hook's invocation everywhere except `--frozen`, which is
# deliberately on this side only — the hook re-locks first, a check must not. This is the
# test that catches drift in either direction, including someone "restoring" the symmetry.


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
        self.assertNotIn("--no-hashes", cmd)
        self.assertIn("--no-emit-project", cmd)
        # Without this the check re-locks the tree it is checking, repairing staleness in the
        # runner and comparing against a file the commit does not contain.
        self.assertIn("--frozen", cmd)
        self.assertEqual(cmd[cmd.index("--format") + 1], "requirements-txt")


# ── TestCheckUvLockCurrent ─────────────────────────────────────────────────────
# The argv contract matters as much as the return code: `uv lock` without --check
# would *rewrite* the lock from a CI job instead of reporting it, which is the
# behaviour this backstop exists to avoid.


class TestCheckUvLockCurrent(unittest.TestCase):
    def _run_with(self, side_effect):
        with (
            mock.patch("subprocess.run", side_effect=side_effect) as run,
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            rc = check_modules.check_uv_lock_current(Path("/fake"))
        return rc, stdout.getvalue(), run

    def test_passes_and_only_checks(self):
        rc, _, run = self._run_with(lambda *a, **k: mock.Mock(returncode=0))
        self.assertEqual(rc, 0)
        self.assertEqual(run.call_args.args[0], ["uv", "lock", "--check"])

    def test_stale_lock_reports_and_names_the_fix(self):
        def fake(*_a, **_k):
            raise subprocess.CalledProcessError(2, ["uv"], "", "the lockfile is not up-to-date")

        rc, out, _ = self._run_with(fake)
        self.assertEqual(rc, 1)
        self.assertIn("uv.lock", out)
        self.assertIn("uv lock", out)

    def test_missing_uv_is_not_a_failure(self):
        # Same posture as the export check: this runs wherever check_modules.py runs, and a
        # host without uv has nothing to say about the lock.
        def fake(*_a, **_k):
            raise FileNotFoundError

        rc, _, _ = self._run_with(fake)
        self.assertEqual(rc, 0)


# ── TestCheck ──────────────────────────────────────────────────────────────────
# Driver-level wiring. Mocks each per-language and per-invariant check function to
# return preset error counts; asserts check() aggregates correctly and prints the
# success message only when every check returns 0. Catches "added a check but
# forgot to wire it into check()" regressions -- main() only collapses the count
# to a status, so an invariant wired into main() instead would never run.


class TestCheck(unittest.TestCase):
    def _run(self, **return_values: int) -> tuple[int, str]:
        """Run check_modules.check() with mocks for every check function.

        return_values keys: 'configs_go', 'configs_python', 'matrices', 'py_root',
        'py_members', 'uv_lock', 'uv_current'. Missing keys default to 0.
        """
        configs_results = {
            "go": return_values.get("configs_go", 0),
            "python": return_values.get("configs_python", 0),
        }

        def fake_configs(_root, language, _modules):
            return configs_results[language.name]

        with (
            tempfile.TemporaryDirectory() as tmp,
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
            # Mocked like the rest, and load-bearing: unmocked it would shell out to `uv`
            # against the real workspace from inside a unit test.
            mock.patch.object(
                check_modules,
                "check_uv_lock_current",
                return_value=return_values.get("uv_current", 0),
            ),
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            rc = check_modules.check(Path(tmp))
        return rc, stdout.getvalue()

    def test_all_clean_prints_success(self):
        rc, out = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("consistent", out)

    def test_aggregates_error_counts(self):
        # uv_current stays 0 here: the two uv checks are sequential, not summed, so a case
        # with both set would measure the suppression below rather than the aggregation.
        rc, _ = self._run(
            configs_go=2, configs_python=1, matrices=3, py_root=1, py_members=2, uv_lock=1
        )
        self.assertEqual(rc, 10)

    def test_a_stale_lock_suppresses_the_export_diff(self):
        # `uv export --frozen` also fails when the lock is stale, and its message names
        # requirements_lock.txt — the innocent file. One cause must produce one message.
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(check_modules, "workspace_root", return_value=Path(tmp)),
            mock.patch.object(check_modules, "check_module_configs", return_value=0),
            mock.patch.object(check_modules, "check_workflow_matrices", return_value=0),
            mock.patch.object(check_modules, "check_python_workspace_root", return_value=0),
            mock.patch.object(check_modules, "check_python_workspace_members", return_value=0),
            mock.patch.object(check_modules, "check_uv_lock_current", return_value=1),
            mock.patch.object(check_modules, "check_uv_lock_fresh", return_value=1) as fresh,
            mock.patch("sys.stdout", new_callable=io.StringIO),
        ):
            rc = check_modules.main()

        self.assertEqual(rc, 1)
        fresh.assert_not_called()

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


class TestMainExitStatus(unittest.TestCase):
    """main() reports pass/fail, never the finding count -- see _workspace.exit_status."""

    def test_a_truncating_count_still_fails(self):
        with (
            mock.patch.object(check_modules, "workspace_root", return_value=Path("/fake")),
            mock.patch.object(check_modules, "check", return_value=256),
        ):
            self.assertEqual(check_modules.main(), 1)

    def test_no_findings_still_passes(self):
        with (
            mock.patch.object(check_modules, "workspace_root", return_value=Path("/fake")),
            mock.patch.object(check_modules, "check", return_value=0),
        ):
            self.assertEqual(check_modules.main(), 0)

    def test_main_delegates_to_check_at_workspace_root(self):
        # Without this, `check(Path.cwd())` in main() passes the whole suite.
        with (
            mock.patch.object(
                check_modules, "workspace_root", return_value=Path("/fake/root")
            ) as wr,
            mock.patch.object(check_modules, "check", return_value=0) as inner,
        ):
            self.assertEqual(check_modules.main(), 0)
        wr.assert_called_once_with()
        inner.assert_called_once_with(Path("/fake/root"))


if __name__ == "__main__":
    unittest.main()
