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
            self.assertEqual(cpv.check_workflows(root, "3.14"), [])

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

    def test_both_inputs_is_caught(self):
        """setup-python prefers python-version, so the file is dead config that reads as live.

        Regression: the line-oriented predecessor flagged every literal line regardless of what
        sat beside it, so this shape was caught before the structural rewrite lost it.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(
                root,
                ".github/workflows/ci.yml",
                "jobs:\n  a:\n    steps:\n"
                "      - uses: actions/setup-python@abc\n"
                "        with:\n"
                "          python-version-file: .python-version\n"
                '          python-version: "3.11"\n',
            )
            problems = cpv.check_workflows(root)
            self.assertEqual(len(problems), 1)
            self.assertIn("both python-version and python-version-file", problems[0])

    def test_both_inputs_is_caught_even_when_the_literal_is_an_expression(self):
        """`${{ }}` earns a pass on its own, but not alongside a file the action will ignore."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(
                root,
                ".github/workflows/ci.yml",
                "jobs:\n  a:\n    steps:\n"
                "      - uses: actions/setup-python@abc\n"
                "        with:\n"
                "          python-version-file: .python-version\n"
                "          python-version: ${{ matrix.python-version }}\n",
            )
            problems = cpv.check_workflows(root)
            self.assertEqual(len(problems), 1)
            self.assertIn("both python-version and python-version-file", problems[0])

    def test_non_scalar_literal_is_described_not_rendered_as_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(
                root,
                ".github/workflows/ci.yml",
                "jobs:\n  a:\n    steps:\n"
                "      - uses: actions/setup-python@abc\n"
                "        with:\n          python-version: [3.12, 3.13]\n",
            )
            problems = cpv.check_workflows(root)
            self.assertEqual(len(problems), 1)
            self.assertNotIn("None", problems[0])
            self.assertIn("a list or mapping", problems[0])

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
            # The action does find `.python-version` by cwd lookup; what is wrong is that the
            # dependency is implicit and breaks under `working-directory:`, not that it is absent.
            self.assertIn("names no version input", problems[0])
            self.assertIn("cwd lookup", problems[0])

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


def matrix_workflow(axis_values: str, expression: str = "${{ matrix.python-version }}") -> str:
    return (
        "jobs:\n  a:\n"
        f"{axis_values}"
        "    steps:\n"
        "      - uses: actions/setup-python@abc\n"
        f"        with:\n          python-version: {expression}\n"
    )


AXIS = '    strategy:\n      matrix:\n        python-version: ["3.12", "3.13", "3.14"]\n'


class TestMatrixDrivenSteps(unittest.TestCase):
    """A `${{ matrix.x }}` pass-through relocates the level into the axis, so the axis is checked.

    Testing more versions than the pin is the point (#272). Testing fewer means the job stops
    exercising the version the rest of the repo targets, the moment the pin advances.
    """

    def _run(self, workflow: str, version: str | None = "3.14") -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, ".github/workflows/ci.yml", workflow)
            return cpv.check_workflows(root, version)

    def test_axis_containing_the_pin_passes(self):
        self.assertEqual(self._run(matrix_workflow(AXIS)), [])

    def test_axis_missing_the_pin_is_caught(self):
        axis = '    strategy:\n      matrix:\n        python-version: ["3.12", "3.13"]\n'
        problems = self._run(matrix_workflow(axis))
        self.assertEqual(len(problems), 1)
        self.assertIn("does not include", problems[0])

    def test_include_can_supply_the_pin(self):
        axis = (
            "    strategy:\n      matrix:\n"
            '        python-version: ["3.12"]\n'
            '        include:\n          - python-version: "3.14"\n'
        )
        self.assertEqual(self._run(matrix_workflow(axis)), [])

    def test_reference_to_an_undefined_axis_is_caught(self):
        problems = self._run(matrix_workflow(""))
        self.assertEqual(len(problems), 1)
        self.assertIn("does not define", problems[0])

    def test_a_computed_matrix_is_caught_rather_than_waved_through(self):
        axis = "    strategy:\n      matrix: ${{ fromJSON(needs.x.outputs.m) }}\n"
        problems = self._run(matrix_workflow(axis))
        self.assertEqual(len(problems), 1)
        self.assertIn("does not define", problems[0])

    def test_a_non_matrix_expression_is_caught(self):
        problems = self._run(matrix_workflow(AXIS, "${{ env.PYVER }}"))
        self.assertEqual(len(problems), 1)
        self.assertIn("cannot resolve", problems[0])

    def test_axis_is_unchecked_when_the_pin_is_unreadable(self):
        """Structural problems still report without a pin; only the value comparison needs one."""
        axis = '    strategy:\n      matrix:\n        python-version: ["3.12"]\n'
        self.assertEqual(self._run(matrix_workflow(axis), version=None), [])


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
        version, pin_problems = cpv.read_pin(self.root)
        self.assertEqual(pin_problems, [])
        self.assertEqual(cpv.check_workflows(self.root, version), [])


if __name__ == "__main__":
    unittest.main()
