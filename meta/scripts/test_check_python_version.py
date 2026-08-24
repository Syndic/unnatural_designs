"""Tests for check_python_version.py."""

import tempfile
import unittest
from pathlib import Path

from meta.scripts import check_python_version as cpv


def write(root: Path, rel: str, content: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def write_pin(root: Path, version: str = "3.14\n") -> None:
    write(root, cpv.PIN_FILE, version)


class TestReadPin(unittest.TestCase):
    def test_reads_a_bare_minor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_pin(root)
            self.assertEqual(cpv.read_pin(root), ("3.14", []))

    def test_missing_file_is_a_problem(self):
        with tempfile.TemporaryDirectory() as tmp:
            version, problems = cpv.read_pin(Path(tmp))
            self.assertIsNone(version)
            self.assertIn("missing", problems[0])

    def test_patch_segment_is_rejected(self):
        """A three-segment pin is a scheduled break: rules_python ships one patch per minor."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_pin(root, "3.14.4\n")
            version, problems = cpv.read_pin(root)
            self.assertIsNone(version)
            self.assertIn("<major>.<minor>", problems[0])

    def test_missing_trailing_newline_is_a_problem(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_pin(root, "3.14")
            version, problems = cpv.read_pin(root)
            self.assertEqual(version, "3.14")
            self.assertIn("trailing newline", problems[0])


class TestPyproject(unittest.TestCase):
    def test_matching_floor_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "pyproject.toml", '[project]\nrequires-python = ">=3.14"\n')
            self.assertEqual(cpv.check_pyproject(root, "3.14"), [])

    def test_stale_floor_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "pyproject.toml", '[project]\nrequires-python = ">=3.13"\n')
            problems = cpv.check_pyproject(root, "3.14")
            self.assertEqual(len(problems), 1)
            self.assertIn("pyproject.toml:2:", problems[0])

    def test_exact_pin_is_not_a_floor(self):
        """`==3.14` would stop ruff and ty inferring the level the rest of the repo uses."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "pyproject.toml", '[project]\nrequires-python = "==3.14"\n')
            self.assertEqual(len(cpv.check_pyproject(root, "3.14")), 1)


class TestModuleBazel(unittest.TestCase):
    def test_every_call_site_must_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(
                root,
                "MODULE.bazel",
                'python.toolchain(python_version = "3.14")\npip.parse(python_version = "3.13")\n',
            )
            problems = cpv.check_module_bazel(root, "3.14")
            self.assertEqual(len(problems), 1)
            self.assertIn("MODULE.bazel:2:", problems[0])

    def test_all_matching_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "MODULE.bazel", 'python.toolchain(python_version = "3.14")\n')
            self.assertEqual(cpv.check_module_bazel(root, "3.14"), [])


class TestDockerfile(unittest.TestCase):
    def test_stale_arg_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, ".devcontainer/Dockerfile", "ARG PYTHON_VERSION=3.13\n")
            problems = cpv.check_dockerfile(root, "3.14")
            self.assertEqual(len(problems), 1)
            self.assertIn("PYTHON_VERSION", problems[0])

    def test_matching_arg_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, ".devcontainer/Dockerfile", "ARG PYTHON_VERSION=3.14\n")
            self.assertEqual(cpv.check_dockerfile(root, "3.14"), [])


WORKFLOW_OK = """\
jobs:
  a:
    steps:
      - uses: actions/setup-python@abc # v7.0.0
        with:
          python-version-file: .python-version
"""


class TestWorkflows(unittest.TestCase):
    def test_version_file_reference_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, ".github/workflows/ci.yml", WORKFLOW_OK)
            self.assertEqual(cpv.check_workflows(root), [])

    def test_hardcoded_literal_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(
                root,
                ".github/workflows/ci.yml",
                "jobs:\n  a:\n    steps:\n"
                "      - uses: actions/setup-python@abc\n"
                '        with:\n          python-version: "3.14"\n',
            )
            problems = cpv.check_workflows(root)
            self.assertTrue(any("hardcoded python-version" in p for p in problems))

    def test_pyproject_as_version_file_is_caught(self):
        """setup-python resolves requires-python as a range and installs the newest match."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(
                root,
                ".github/workflows/ci.yml",
                "jobs:\n  a:\n    steps:\n"
                "      - uses: actions/setup-python@abc\n"
                "        with:\n          python-version-file: pyproject.toml\n",
            )
            problems = cpv.check_workflows(root)
            self.assertEqual(len(problems), 1)
            self.assertIn("semver *range*", problems[0])

    def test_setup_python_without_any_pin_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(
                root,
                ".github/workflows/ci.yml",
                "jobs:\n  a:\n    steps:\n      - uses: actions/setup-python@abc\n",
            )
            problems = cpv.check_workflows(root)
            self.assertEqual(len(problems), 1)
            self.assertIn("must pin the level explicitly", problems[0])

    def test_yaml_extension_is_also_scanned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(
                root,
                ".github/workflows/other.yaml",
                "jobs:\n  a:\n    steps:\n      - uses: actions/setup-python@abc\n",
            )
            self.assertEqual(len(cpv.check_workflows(root)), 1)

    def test_workflow_without_setup_python_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, ".github/workflows/ci.yml", "jobs:\n  a:\n    steps:\n      - run: true\n")
            self.assertEqual(cpv.check_workflows(root), [])


if __name__ == "__main__":
    unittest.main()
