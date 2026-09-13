"""Guards devcontainer.yml's two required checks against the job whose verdict gates them.

`Base image (all platforms)` and `Build devcontainer and smoke test` are the names branch
protection requires, and both hang off one classification job. A `changes` that *fails* emits the
same empty outputs a `changes` that matched nothing emits, so without the guards held here the
failure is invisible in the worst possible direction: `base-image` skips on an unevaluated gate,
`base-image-all` reads that as the ordinary path-gated skip and passes, and `build-and-smoke-test`
is removed from the run entirely — which GitHub reports as `skipped`, and counts as a pass. Both
required checks go green over a workflow that classified nothing.

Three couplings keep that closed, and none of them fails while it drifts:

  - **The `--emit` arguments against `SETS`.** `select()` refuses a name it does not know, which
    is what makes a renamed set a *failed* `changes` rather than a silently false gate — and
    nothing else in the tree reads devcontainer.yml's copy of those names.
    `//meta/scripts:test_classify_changed_paths` tests the refusal, `:test_path_classification_
    pattern_sets` tests the sets; neither opens a workflow. Same shape for the job's `outputs:`
    block and the gates that read it: a name that agrees with nothing is `false` on every run.
  - **`base-image-all` asking two questions.** Accepting `skipped` from the matrix is correct and
    stays; what it cannot do is infer from that skip that the gate ran.
  - **`build-and-smoke-test` surviving a failed dependency.** A job with no condition is deleted
    when a `needs:` fails, and a deleted required check is a green one.

The fan-in shells are run rather than matched, for the reason
`//meta/scripts:test_codeql_toolchain` gives: asserting on their spelling would fail an idiom that
behaves identically, and pass one that does not.
"""

import re
import subprocess
import unittest
from pathlib import Path

import yaml
from meta.scripts.path_classification_pattern_sets import SETS

# Not .resolve(): the workflow is a cross-package data dep, so it lives in the runfiles tree beside
# this file rather than at the source path a resolved symlink would lead back to.
_WORKFLOW = Path(__file__).parent.parent.parent / ".github/workflows/devcontainer.yml"
_JOBS = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))["jobs"]

# The names branch protection holds. They are strings in repo settings, which nothing here can
# read, so the coupling this file can hold is between the job key and the name it reports under.
_REQUIRED_CHECKS = {
    "base-image-all": "Base image (all platforms)",
    "build-and-smoke-test": "Build devcontainer and smoke test",
}

# The two guard steps, by name. `build-and-smoke-test`'s position is what makes it a guard: it
# runs before the job pays for an npm install and a container build it has been told not to trust.
_FAN_IN_STEP = "Verify every base-image row passed"
_GUARD_STEP = "Verify the classification ran"

_EMIT_RE = re.compile(r"--emit\s+(\S+)")
_GATE_RE = re.compile(r"needs\.changes\.outputs\.(\w+)")
_STEP_OUTPUT_RE = re.compile(r"^\$\{\{\s*steps\.(\w+)\.outputs\.(\w+)\s*\}\}$")

# Every result a `needs:` job can report other than success. `skipped` is the one that matters:
# it is what a failed dependency and a false `if:` both produce, and what GitHub counts as a pass.
_NOT_SUCCESS = ("failure", "cancelled", "skipped")


def classify_step() -> dict:
    """The step that runs the classifier — the one the job's `outputs:` read back from."""
    for step in _JOBS["changes"]["steps"]:
        if _EMIT_RE.search(step.get("run", "")):
            return step
    raise AssertionError(f"no step running the classifier in {_WORKFLOW.name}'s `changes` job")


def emitted_sets() -> list[str]:
    """The pattern sets devcontainer.yml asks the classifier for, in the order it asks."""
    return _EMIT_RE.findall(classify_step()["run"])


def needs(job: str) -> list[str]:
    """A job's dependencies. GitHub takes a bare scalar too, and `sorted()` would shred one."""
    declared = _JOBS[job].get("needs", [])
    return [declared] if isinstance(declared, str) else declared


def run_guard(job: str, step: str, results: dict[str, str]) -> subprocess.CompletedProcess:
    """Run a job's guard shell with each `needs.<job>.result` replaced by a given outcome."""
    script = next((s["run"] for s in _JOBS[job]["steps"] if s.get("name") == step), None)
    if script is None:
        raise AssertionError(f"no `{step}` step in {_WORKFLOW.name}'s `{job}` job")
    for needed, result in results.items():
        # Concatenated rather than formatted: every brace in the expression is literal.
        expression = "${{ needs." + needed + ".result }}"
        # Non-vacuity: a substitution that matched nothing would run the same shell every time,
        # and every assertion below would pass on whichever branch it happened to take.
        if expression not in script:
            raise AssertionError(f"`{job}` does not read {expression}")
        script = script.replace(expression, result)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


class RequiredCheckNamesTest(unittest.TestCase):
    """The ruleset holds these strings literally, and no test can read the ruleset."""

    def test_each_required_job_reports_under_the_name_that_is_required(self):
        for job, name in _REQUIRED_CHECKS.items():
            with self.subTest(job=job):
                self.assertEqual(
                    _JOBS[job]["name"],
                    name,
                    "renaming this job decouples it from the required-status-check context, "
                    "which is repo settings — rename both, or neither",
                )


class ClassificationOutputsTest(unittest.TestCase):
    """The three hops between a pattern set and a gated step, each silent when it breaks."""

    def setUp(self):
        self.emitted = emitted_sets()
        self.outputs = _JOBS["changes"]["outputs"]

    def test_every_emitted_set_exists(self):
        """`select()` fails the job on a name it does not know; this is what sees that coming."""
        self.assertTrue(self.emitted, f"no `--emit` arguments found in {_WORKFLOW.name}")
        self.assertEqual(
            sorted(name for name in self.emitted if name not in SETS),
            [],
            "devcontainer.yml names a pattern set that path_classification_pattern_sets.py does "
            "not define — the classifier exits non-zero, and the `changes` job fails",
        )

    def test_the_job_declares_exactly_what_it_emits(self):
        self.assertEqual(
            sorted(self.outputs),
            sorted(self.emitted),
            "a set emitted but not declared reaches no consumer, and a declared set the "
            "classifier was never asked for is `false` on every run",
        )

    def test_each_output_carries_its_own_set_from_the_step_that_emitted_it(self):
        """A crossed or misspelled name here is an empty string, which reads as `false`."""
        for name, expression in self.outputs.items():
            with self.subTest(output=name):
                hit = _STEP_OUTPUT_RE.match(expression)
                self.assertIsNotNone(hit, f"`{name}` is not a plain step-output reference")
                self.assertEqual(hit.group(1), classify_step()["id"])
                self.assertEqual(hit.group(2), name)

    def test_every_gate_reads_an_output_the_job_declares(self):
        """A gated step is the one place a typo costs nothing at all: it just never runs."""
        gates = set(_GATE_RE.findall(_WORKFLOW.read_text(encoding="utf-8")))
        self.assertTrue(gates, f"nothing in {_WORKFLOW.name} gates on the classification")
        self.assertEqual(sorted(gates - set(self.outputs)), [])


class BaseImageFanInTest(unittest.TestCase):
    """`skipped` from the matrix is legitimate; `skipped` because the gate never ran is not."""

    def setUp(self):
        self.job = _JOBS["base-image-all"]

    def test_the_fan_in_depends_on_both_the_matrix_and_the_gate(self):
        self.assertEqual(
            sorted(needs("base-image-all")),
            ["base-image", "changes"],
            "a fan-in that does not depend on the gate cannot tell a matrix the gate skipped "
            "from one it never reached",
        )

    def test_the_fan_in_runs_even_when_the_matrix_fails(self):
        self.assertEqual(
            self.job["if"],
            "always()",
            "without it a failed matrix skips this job, and branch protection counts a skipped "
            "required check as passed",
        )

    def test_a_gated_off_matrix_passes(self):
        """The ordinary path: most PRs touch nothing the base image is assembled from."""
        for result in ("success", "skipped"):
            with self.subTest(base_image=result):
                done = run_guard(
                    "base-image-all", _FAN_IN_STEP, {"changes": "success", "base-image": result}
                )
                self.assertEqual(done.returncode, 0, f"{done.stdout}{done.stderr}".strip())

    def test_a_matrix_that_failed_or_was_cancelled_fails(self):
        for result in ("failure", "cancelled"):
            with self.subTest(base_image=result):
                done = run_guard(
                    "base-image-all", _FAN_IN_STEP, {"changes": "success", "base-image": result}
                )
                self.assertNotEqual(done.returncode, 0)

    def test_a_classification_that_did_not_succeed_fails(self):
        """Including `skipped`, which is exactly what a failed `changes` leaves behind."""
        for result in _NOT_SUCCESS:
            with self.subTest(changes=result):
                done = run_guard(
                    "base-image-all", _FAN_IN_STEP, {"changes": result, "base-image": "skipped"}
                )
                self.assertNotEqual(
                    done.returncode,
                    0,
                    "a matrix skipped by a gate that did not run is byte-identical to one the "
                    "gate deliberately skipped; the fan-in has to ask about the gate itself",
                )


class BuildAndSmokeTestGuardTest(unittest.TestCase):
    """The required check that is deleted, rather than failed, by a failed dependency."""

    def setUp(self):
        self.job = _JOBS["build-and-smoke-test"]

    def test_the_job_survives_a_failed_classification(self):
        self.assertEqual(
            self.job["if"],
            "${{ !cancelled() }}",
            "with no condition this job is skipped when `changes` fails, and a skipped required "
            "check passes; `always()` would fix that and undo the concurrency group's "
            "supersession, starting a devcontainer build for a run already replaced",
        )

    def test_the_guard_runs_before_the_job_does_any_work(self):
        self.assertEqual(
            self.job["steps"][0].get("name"),
            _GUARD_STEP,
            "every step after this one is gated on an output that reads empty when the "
            "classification failed, so the job would do its skip-path work and report green",
        )

    def test_the_guard_passes_a_successful_classification(self):
        done = run_guard("build-and-smoke-test", _GUARD_STEP, {"changes": "success"})
        self.assertEqual(done.returncode, 0, f"{done.stdout}{done.stderr}".strip())

    def test_the_guard_fails_anything_else(self):
        for result in _NOT_SUCCESS:
            with self.subTest(changes=result):
                done = run_guard("build-and-smoke-test", _GUARD_STEP, {"changes": result})
                self.assertNotEqual(
                    done.returncode,
                    0,
                    "an unevaluated gate leaves every `needs.changes.outputs.*` empty, which is "
                    "the same thing this job's skip path reports success for",
                )


if __name__ == "__main__":
    unittest.main()
