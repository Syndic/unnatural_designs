"""Covers `_workflows.py` — reading module matrices out of workflow YAML.

Two groups. The first is carried over from the line-oriented predecessor and asserts what the
check needs from any implementation: which jobs have a matrix, what is in it, and where.

The second is the shape suite, and it is the point of the parse. Every entry in it is a real
YAML spelling of a matrix, and the contract is that each one lands in exactly one bucket —
read correctly, or reported as un-checkable. Nothing may be both unread and silent, which is
the state `go_module: []` was in, and which a widened set of text patterns then reproduced for
flow mappings while newly mis-firing on ordinary keys outside a matrix.
"""

import tempfile
import textwrap
import unittest
from pathlib import Path

from meta.scripts._workflows import unrecognised_matrix_keys, workflow_matrix_lists

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

    def test_matrix_key_with_empty_list(self):
        """A declared-but-empty entry list still reports the block, with no entries.

        Callers rely on this to emit "missing entry" diagnostics anchored at key_line — an
        empty list is a matrix that exists and is incomplete, not an absent matrix.
        """
        content = textwrap.dedent("""\
            jobs:
              scan:
                strategy:
                  matrix:
                    go_module:
                steps:
                  - run: echo hi
            """)
        self.assertEqual(self._parse(content), [("scan", 5, {})])

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


# ── TestMatrixShapes ──────────────────────────────────────────────────────────
# One case per real YAML spelling. The contract every case is held to: read correctly, or
# reported — never both unread and silent.


def _workflow(matrix_body: str) -> str:
    return textwrap.dedent("""\
        name: T
        on: [push]
        jobs:
          govulncheck:
            runs-on: ubuntu-latest
            strategy:
              matrix:
        {body}
            steps:
              - run: echo hi
        """).replace("{body}", matrix_body)


class TestMatrixShapes(unittest.TestCase):
    def _both(self, matrix_body: str, matrix_key: str = "go_module"):
        """(entry paths, {line: problem}) for one matrix spelling."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "workflow.yml"
            path.write_text(_workflow(matrix_body))
            blocks = workflow_matrix_lists(path, matrix_key)
            problems = unrecognised_matrix_keys(path, matrix_key)
        return {entry for _, _, mods in blocks for entry in mods}, problems

    def _whole(self, workflow: str, matrix_key: str = "go_module"):
        """Same, for a case that needs to control the whole file rather than the matrix body."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "workflow.yml"
            path.write_text(textwrap.dedent(workflow))
            blocks = workflow_matrix_lists(path, matrix_key)
            problems = unrecognised_matrix_keys(path, matrix_key)
        return {entry for _, _, mods in blocks for entry in mods}, problems

    # ── Spellings of the same matrix, all of which must read the same ─────────

    def test_equivalent_spellings_all_read_the_same(self):
        """Block, flow, quoted and commented forms are one matrix written four ways."""
        for name, body in {
            "block list": "        go_module:\n          - tools/foo",
            "flow sequence": "        go_module: [tools/foo]",
            "quoted key": '        "go_module":\n          - tools/foo',
            "comment on the key": "        go_module: # the modules\n          - tools/foo",
            "comment on the item": "        go_module:\n          - tools/foo # the tool",
            "inline whole matrix": "        go_module: [tools/foo]",
        }.items():
            with self.subTest(shape=name):
                entries, problems = self._both(body)
                self.assertEqual(entries, {Path("tools/foo")})
                self.assertEqual(problems, {})

    def test_include_forms(self):
        for name, body in {
            "simple": "        include:\n          - go_module: tools/foo",
            "flow mapping": "        include:\n          - { go_module: tools/foo }",
            "second axis": (
                '        include:\n          - go_module: tools/foo\n            go_version: "1.27"'
            ),
            "key not first": (
                '        include:\n          - go_version: "1.27"\n            go_module: tools/foo'
            ),
        }.items():
            with self.subTest(shape=name):
                entries, problems = self._both(body)
                self.assertEqual(entries, {Path("tools/foo")})
                self.assertEqual(problems, {})

    def test_both_forms_in_one_matrix_are_unioned(self):
        """GitHub unions them; two blocks would report each one's entries missing from the other."""
        entries, problems = self._both(
            "        go_module:\n          - tools/foo\n"
            "        include:\n          - go_module: tools/bar"
        )
        self.assertEqual(entries, {Path("tools/foo"), Path("tools/bar")})
        self.assertEqual(problems, {})

    def test_declared_but_empty_is_a_block_with_no_entries(self):
        """An empty axis is an incomplete matrix, not an absent one — callers anchor on it."""
        for body in ("        go_module:", "        go_module: []"):
            with self.subTest(body=body):
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / "workflow.yml"
                    path.write_text(_workflow(body))
                    blocks = workflow_matrix_lists(path, "go_module")
                self.assertEqual(len(blocks), 1)
                self.assertEqual(blocks[0][2], {})

    # ── exclude: is recognised, and contributes nothing either way ────────────
    # Decided policy, not a fact about YAML: coverage is the base axis plus `include:`, and
    # `exclude:` is ignored in both directions. A module excluded for cause has been handled
    # appropriately, which is what the check is asking about — so an exclusion neither grants
    # coverage nor withdraws it. Recorded here because the alternatives are each plausible
    # enough to be re-proposed: collecting excludes reads "someone thought about it", and
    # subtracting them reads "GitHub will not run it".

    def test_excluding_a_module_leaves_it_covered(self):
        """Exclusion is a way of handling a module, so it does not withdraw coverage."""
        for name, body in {
            "partial exclude": (
                "        go_module:\n          - tools/foo\n        os: [linux, mac]\n"
                "        exclude:\n          - go_module: tools/foo\n            os: mac"
            ),
            "total exclude": (
                "        go_module:\n          - tools/foo\n"
                "        exclude:\n          - go_module: tools/foo"
            ),
        }.items():
            with self.subTest(shape=name):
                entries, problems = self._both(body)
                self.assertEqual(entries, {Path("tools/foo")})
                self.assertEqual(
                    problems, {}, "a valid exclude: must not be reported as un-checkable"
                )

    def test_excluding_a_module_does_not_grant_it_coverage(self):
        """The half with teeth: `exclude:` cannot put a module into the run set, so naming one
        there and nowhere else leaves it uncovered, and the caller reports it missing.

        GitHub applies `exclude:` as a filter over the axes, so an entry naming a value that is
        in no axis matches nothing. Such a workflow is malformed rather than clever.
        """
        entries, problems = self._both(
            "        go_module:\n          - tools/foo\n"
            "        exclude:\n          - go_module: tools/bar"
        )
        self.assertEqual(entries, {Path("tools/foo")})
        self.assertNotIn(Path("tools/bar"), entries)
        self.assertEqual(problems, {})

    # ── Scoping: the key only counts where a matrix actually is ───────────────

    def test_the_key_elsewhere_in_the_file_is_not_a_matrix(self):
        """Ordinary YAML using the same identifier is none of this check's business."""
        cases = {
            "workflow_call input": """
                name: T
                on:
                  workflow_call:
                    inputs:
                      go_module:
                        type: string
                jobs:
                  a:
                    runs-on: ubuntu-latest
                    steps:
                      - run: echo hi
                """,
            "job env": """
                name: T
                on: [push]
                jobs:
                  a:
                    runs-on: ubuntu-latest
                    env:
                      go_module: tools/foo
                    steps:
                      - run: echo hi
                """,
            "step with:": """
                name: T
                on: [push]
                jobs:
                  a:
                    runs-on: ubuntu-latest
                    steps:
                      - uses: some/action@v1
                        with:
                          go_module: tools/foo
                """,
            "inside a run block": """
                name: T
                on: [push]
                jobs:
                  a:
                    runs-on: ubuntu-latest
                    steps:
                      - run: |
                          echo config
                          go_module: tools/foo
                """,
        }
        for name, workflow in cases.items():
            with self.subTest(shape=name):
                entries, problems = self._whole(workflow)
                self.assertEqual(entries, set())
                self.assertEqual(problems, {}, "this is not a matrix and must not be reported")

    def test_the_key_nested_inside_an_include_item_is_not_an_entry(self):
        """A same-named key under an item's own sub-mapping is not that item's axis value."""
        entries, problems = self._both(
            "        include:\n          - name: x\n            env:\n"
            "              go_module: tools/deep"
        )
        self.assertEqual(entries, set())
        self.assertEqual(problems, {})

    # ── What genuinely cannot be checked is reported ──────────────────────────

    def test_a_computed_matrix_is_reported(self):
        """`fromJSON` is a real feature and no static check can resolve it — say so."""
        entries, problems = self._both("        go_module: ${{ fromJSON(needs.x.outputs.m) }}")
        self.assertEqual(entries, set())
        self.assertTrue(problems, "an unresolvable axis must not pass as an absent one")

    def test_a_whole_computed_matrix_is_reported(self):
        entries, problems = self._whole("""
            name: T
            on: [push]
            jobs:
              a:
                runs-on: ubuntu-latest
                strategy:
                  matrix: ${{ fromJSON(needs.x.outputs.m) }}
                steps:
                  - run: echo hi
            """)
        self.assertEqual(entries, set())
        self.assertTrue(problems)

    def test_unparseable_yaml_is_reported(self):
        """A workflow that is not YAML must fail loudly rather than read as having no matrix."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "workflow.yml"
            path.write_text("jobs:\n  a:\n   - [unbalanced\n")
            self.assertTrue(unrecognised_matrix_keys(path, "go_module"))

    def test_the_repos_own_workflows_are_clean(self):
        """Held against real files: a false positive here fails CI for everyone."""
        workflows = Path(__file__).parent.parent.parent / ".github" / "workflows"
        found = sorted(workflows.glob("*.yml"))
        # Without the //:workflows data dep this directory is absent from the runfiles tree and
        # the loop below runs zero times — passing while checking nothing.
        self.assertTrue(found, "no workflow files in the runfiles tree; this test is vacuous")
        for wf in found:
            with self.subTest(workflow=wf.name):
                self.assertEqual(unrecognised_matrix_keys(wf, "go_module"), {})


if __name__ == "__main__":
    unittest.main()
