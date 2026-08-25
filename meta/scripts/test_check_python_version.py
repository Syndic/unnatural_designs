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
    def test_matching_exact_minor_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "pyproject.toml", '[project]\nrequires-python = "==3.14.*"\n')
            self.assertEqual(cpv.check_pyproject(root, "3.14"), [])

    def test_stale_minor_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "pyproject.toml", '[project]\nrequires-python = "==3.13.*"\n')
            problems = cpv.check_pyproject(root, "3.14")
            self.assertEqual(len(problems), 1)
            self.assertIn("pyproject.toml:2:", problems[0])

    def test_open_floor_is_caught(self):
        """`>=3.14` admits 3.15, and Renovate's `replace` never rewrites a range that fits."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "pyproject.toml", '[project]\nrequires-python = ">=3.14"\n')
            self.assertEqual(len(cpv.check_pyproject(root, "3.14")), 1)

    def test_patchless_equality_is_caught(self):
        """`==3.14` matches only 3.14.0 -- it would reject the interpreters actually in use."""
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

# Every shape a line-oriented scan got wrong, in one file. The first three were false positives
# and the fourth was invisible to it; see the module docstring of check_python_version and #268.
WORKFLOW_LOOKALIKES = """\
jobs:
  a:
    strategy:
      matrix:
        python-version: ["3.12", "3.13", "3.14"]
    steps:
      - uses: actions/setup-python@abc
        with:
          python-version: ${{ matrix.python-version }}
  b:
    steps:
      - run: |
          cat <<EOF > cfg.yml
          python-version: 3.13
          EOF
  c:
    env:
      python-version: "3.11"
    steps:
      - uses: actions/setup-python@abc
        with:
          python-version-file: .python-version
"""


class TestWorkflows(unittest.TestCase):
    def test_version_file_reference_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, ".github/workflows/ci.yml", WORKFLOW_OK)
            self.assertEqual(cpv.check_workflows(root), [])

    def test_lookalikes_outside_a_setup_python_step_are_ignored(self):
        """A matrix axis, a `${{ }}` pass-through, a `run:` heredoc, a job `env:` -- not pins."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, ".github/workflows/ci.yml", WORKFLOW_LOOKALIKES)
            self.assertEqual(cpv.check_workflows(root), [])

    def test_flow_style_literal_is_caught(self):
        """The inverse failure: a scan missed this shape entirely, so a literal passed the guard."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(
                root,
                ".github/workflows/ci.yml",
                "jobs:\n  a:\n    steps:\n"
                '      - {uses: actions/setup-python@x, with: {python-version: "3.13"}}\n',
            )
            problems = cpv.check_workflows(root)
            self.assertEqual(len(problems), 1)
            self.assertIn("hardcoded python-version", problems[0])

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
            self.assertIn("pins nothing", problems[0])

    def test_yaml_extension_is_also_scanned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(
                root,
                ".github/workflows/other.yaml",
                "jobs:\n  a:\n    steps:\n      - uses: actions/setup-python@abc\n",
            )
            self.assertEqual(len(cpv.check_workflows(root)), 1)

    def test_composite_action_is_scanned(self):
        """`.github/actions/*/action.yml` holds steps too, under `runs:` rather than `jobs:`."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(
                root,
                ".github/actions/thing/action.yml",
                "runs:\n  using: composite\n  steps:\n"
                "      - uses: actions/setup-python@abc\n"
                '        with:\n          python-version: "3.14"\n',
            )
            problems = cpv.check_workflows(root)
            self.assertEqual(len(problems), 1)
            self.assertIn("hardcoded python-version", problems[0])

    def test_unparseable_yaml_is_reported_not_skipped(self):
        """A file nobody can read must not be indistinguishable from one with nothing to flag."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, ".github/workflows/ci.yml", "jobs:\n  a:\n   - [unclosed\n")
            problems = cpv.check_workflows(root)
            self.assertEqual(len(problems), 1)
            self.assertIn("could not be parsed as YAML", problems[0])

    def test_workflow_without_setup_python_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, ".github/workflows/ci.yml", "jobs:\n  a:\n    steps:\n      - run: true\n")
            self.assertEqual(cpv.check_workflows(root), [])


class TestAgainstThisRepo(unittest.TestCase):
    """Held against the repo's real YAML, where a false positive fails CI for everyone.

    The root comes from `__file__` rather than `workspace_root()`: under Bazel the test runs in a
    sandbox with no git repo, so shelling out to `git rev-parse` fails outright.
    """

    root = Path(__file__).parent.parent.parent

    def test_both_trees_are_discovered(self):
        files = cpv.action_yaml_files(self.root)
        # Without the //:workflows and //:composite_actions data deps these directories are absent
        # from the runfiles tree, and every assertion below would pass while checking nothing.
        self.assertTrue(
            any(p.parent.name == "workflows" for p in files),
            f"no workflows in the runfiles tree; this test is vacuous: {files}",
        )
        self.assertTrue(
            any(p.name == "action.yml" for p in files),
            f"no composite actions in the runfiles tree; this test is vacuous: {files}",
        )

    def test_repo_is_clean(self):
        self.assertEqual(cpv.check_workflows(self.root), [])


if __name__ == "__main__":
    unittest.main()
