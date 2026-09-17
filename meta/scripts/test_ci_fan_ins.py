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

import subprocess
import unittest
from pathlib import Path
from typing import NamedTuple

import yaml

# Not .resolve(): both files are cross-package data deps, so each lives in the runfiles tree
# beside this one rather than at the source path a resolved symlink would lead back to.
_ROOT = Path(__file__).parent.parent.parent
_WORKFLOW = _ROOT / ".github" / "workflows" / "ci.yml"
_README = _ROOT / "README.md"

_JOBS = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))["jobs"]

# GitHub treats these as the same condition, and a fan-in written either way behaves identically.
_ALWAYS = ("always()", "${{ always() }}")


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


def needs(job: str) -> list[str]:
    """A job's `needs:`, as a list whichever way it was written."""
    declared = _JOBS[job].get("needs", [])
    return [declared] if isinstance(declared, str) else list(declared)


def run_fan_in(job: str, result: str) -> subprocess.CompletedProcess:
    """Run a fan-in's shell with `result` standing in for the outcome of the job it needs.

    The expression is built from the job's own `needs:` rather than passed in beside it, so the two
    cannot disagree. One that matched nothing would leave `${{ ... }}` for bash, which errors on it
    and then takes the branch that exits 0 — a fan-in that accepts every result, in a test written
    to prove it rejects them.
    """
    scripts = [step["run"] for step in _JOBS[job]["steps"] if "run" in step]
    assert len(scripts) == 1, f"{job} has {len(scripts)} `run:` steps; which one is the fan-in?"
    expression = f"${{{{ needs.{needs(job)[0]}.result }}}}"
    assert expression in scripts[0], f"{job}'s script does not read `{expression}`"
    return subprocess.run(
        ["bash", "-c", scripts[0].replace(expression, result)], capture_output=True, text=True
    )


class MatrixCoverageTest(unittest.TestCase):
    def test_every_matrix_job_has_a_fan_in(self):
        self.assertEqual(
            {job for job, body in _JOBS.items() if "matrix" in (body.get("strategy") or {})},
            {fan_in.matrix for fan_in in _FAN_INS.values()},
            "a matrix job's check name carries its row, so a ruleset cannot name it — give it a "
            "fan-in (and add that fan-in here) or it is a job nothing can be required to pass",
        )


class FanInTest(unittest.TestCase):
    """What makes requiring a fan-in mean anything about the rows underneath it."""

    def test_fan_in_depends_on_its_matrix_job(self):
        for job, fan_in in _FAN_INS.items():
            with self.subTest(job=job):
                self.assertEqual(
                    needs(job),
                    [fan_in.matrix],
                    "the fan-in is the required check; a matrix row it does not depend on is a "
                    "row nothing gates",
                )

    def test_fan_in_runs_even_when_the_matrix_fails(self):
        for job in _FAN_INS:
            with self.subTest(job=job):
                self.assertIn(
                    _JOBS[job].get("if"),
                    _ALWAYS,
                    "without `always()` a failed matrix skips the fan-in, and branch protection "
                    "counts a skipped required check as passed",
                )

    def test_fan_in_is_named_what_branch_protection_names(self):
        """The ruleset holds these strings literally, and no test can read the ruleset."""
        for job, fan_in in _FAN_INS.items():
            with self.subTest(job=job):
                self.assertEqual(
                    _JOBS[job].get("name"),
                    fan_in.context,
                    "renaming this job silently decouples it from the required-status-check "
                    "context, which is repo settings — rename both, or neither",
                )

    def test_fan_in_passes_when_every_row_succeeded(self):
        for job in _FAN_INS:
            with self.subTest(job=job):
                done = run_fan_in(job, "success")
                self.assertEqual(done.returncode, 0, f"{done.stdout}{done.stderr}".strip())

    def test_fan_in_fails_on_anything_else(self):
        """Run against the real shell, so the `case` idiom in devcontainer.yml would pass too."""
        for job in _FAN_INS:
            for result in ("failure", "cancelled", "skipped"):
                with self.subTest(job=job, result=result):
                    self.assertNotEqual(
                        run_fan_in(job, result).returncode,
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
