"""ci.yml's fan-ins, and the guard that says a matrix job has one at all.

A matrix job's check name carries the row that produced it (`golangci-lint (tools/foo)`), so it
moves with the matrix and cannot be named in a ruleset. The fan-in is the stable name that can be:
it depends on every row, so requiring it requires all of them. That holds only while it runs when
the matrix does not — `if: always()` — since branch protection counts a skipped required check as
passed.

A matrix job with no fan-in is a check no ruleset can name, and nothing about it looks wrong: it
runs, it reports, and it gates nothing. `MatrixCoverageTest` is the half of this file that catches
that one, since the assertions below can only cover a fan-in someone thought to write.

Scope is ci.yml. security.yml's `codeql-all` is held by //meta/scripts:test_codeql_toolchain,
beside the rest of that job; devcontainer.yml's `base-image-all` is the one fan-in with a different
result policy, since its matrix is path-gated and `skipped` legitimately passes there.
"""

import unittest
from pathlib import Path
from typing import NamedTuple

import yaml
from meta.scripts._workflow_text import job_block, run_fan_in

# Not .resolve(): both files are cross-package data deps, so each lives in the runfiles tree
# beside this one rather than at the source path a resolved symlink would lead back to.
_ROOT = Path(__file__).parent.parent.parent
_WORKFLOW = _ROOT / ".github" / "workflows" / "ci.yml"
_README = _ROOT / "README.md"

_TEXT = _WORKFLOW.read_text(encoding="utf-8")


class FanIn(NamedTuple):
    """A fan-in job: the matrix it covers, and the check name branch protection asks for."""

    matrix: str
    context: str


# Keyed by job id. The contexts are strings in repo settings, which nothing here can read — so the
# coupling this file can hold is between each job and the docs that quote its name.
_FAN_INS = {
    "golangci-lint-all": FanIn("golangci-lint", "golangci-lint (all modules)"),
    "build-and-test-all": FanIn("build-and-test-per-target", "Build and test (all targets)"),
}


# Resolved at import, the way test_codeql_toolchain resolves its job: a fan-in named here and
# absent from the workflow is a broken file rather than a failing assertion, and should read as one.
_BLOCKS = {
    job: (job_block(_TEXT, job, where=_WORKFLOW.name), fan_in) for job, fan_in in _FAN_INS.items()
}


def matrix_jobs(text: str) -> set[str]:
    """Job ids in one workflow that run a `strategy.matrix`."""
    jobs = yaml.safe_load(text)["jobs"]
    return {job for job, body in jobs.items() if "matrix" in (body.get("strategy") or {})}


class MatrixCoverageTest(unittest.TestCase):
    def test_every_matrix_job_has_a_fan_in(self):
        self.assertEqual(
            matrix_jobs(_TEXT),
            {fan_in.matrix for fan_in in _FAN_INS.values()},
            "a matrix job's check name carries its row, so a ruleset cannot name it — give it a "
            "fan-in (and add that fan-in here) or it is a job nothing can be required to pass",
        )


class FanInTest(unittest.TestCase):
    """What makes requiring a fan-in mean anything about the rows underneath it."""

    def test_fan_in_depends_on_its_matrix_job(self):
        for job, (block, fan_in) in _BLOCKS.items():
            with self.subTest(job=job):
                self.assertIn(
                    f"needs: [{fan_in.matrix}]",
                    block,
                    "the fan-in is the required check; a matrix row it does not depend on is a "
                    "row nothing gates",
                )

    def test_fan_in_runs_even_when_the_matrix_fails(self):
        for job, (block, _) in _BLOCKS.items():
            with self.subTest(job=job):
                self.assertIn(
                    "if: always()",
                    block,
                    "without `if: always()` a failed matrix skips the fan-in, and branch "
                    "protection counts a skipped required check as passed",
                )

    def test_fan_in_is_named_what_branch_protection_names(self):
        """The ruleset holds these strings literally, and no test can read the ruleset."""
        for job, (block, fan_in) in _BLOCKS.items():
            with self.subTest(job=job):
                self.assertIn(
                    f"name: {fan_in.context}",
                    block,
                    "renaming this job silently decouples it from the required-status-check "
                    "context, which is repo settings — rename both, or neither",
                )

    def test_fan_in_passes_when_every_row_succeeded(self):
        for job, (block, fan_in) in _BLOCKS.items():
            with self.subTest(job=job):
                done = run_fan_in(block, fan_in.matrix, "success")
                self.assertEqual(done.returncode, 0, f"{done.stdout}{done.stderr}".strip())

    def test_fan_in_fails_on_anything_else(self):
        """Run against the real shell, so the `case` idiom in devcontainer.yml would pass too."""
        for job, (block, fan_in) in _BLOCKS.items():
            for result in ("failure", "cancelled", "skipped"):
                with self.subTest(job=job, result=result):
                    self.assertNotEqual(
                        run_fan_in(block, fan_in.matrix, result).returncode,
                        0,
                        f"a matrix that reports `{result}` left rows unrun; nothing in ci.yml "
                        "gates either matrix on a path diff, so there is no benign reason for one",
                    )


class DocumentedNameTest(unittest.TestCase):
    """README is where a reader learns which contexts the ruleset names; it is not the ruleset."""

    def test_readme_names_every_fan_in(self):
        readme = _README.read_text(encoding="utf-8")
        for fan_in in _FAN_INS.values():
            with self.subTest(context=fan_in.context):
                self.assertIn(fan_in.context, readme)


if __name__ == "__main__":
    unittest.main()
