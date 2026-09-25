"""Guards devcontainer.yml's two required checks against the job whose verdict gates them.

`Base image (all platforms)` and `Build devcontainer and smoke test` are the names branch
protection requires, and both hang off one classification job. A `changes` that *fails* emits the
same empty outputs a `changes` that matched nothing emits, so without the guards held here the
failure is invisible in the worst possible direction: `base-image` skips on an unevaluated gate,
`base-image-all` reads that as the ordinary path-gated skip and passes, and `build-and-smoke-test`
is removed from the run entirely — which GitHub reports as `skipped`, and counts as a pass. Both
required checks go green over a workflow that classified nothing.

Three couplings keep that closed, and none of them fails while it drifts:

  - **The `changes` job's `outputs:` block and the gates that read it.** A name that agrees with
    nothing is `false` on every run. Whether the `--emit` names exist at all is
    `//meta/scripts:test_classify_changed_paths_callers`'s, which holds every caller at once.
  - **`base-image-all` asking two questions.** Accepting `skipped` from the matrix is correct and
    stays; what it cannot do is infer from that skip that the gate ran.
  - **`build-and-smoke-test` surviving a failed dependency.** A job with no condition is deleted
    when a `needs:` fails, and a deleted required check is a green one.

Both checks' names, `base-image-all`'s `always()` and how its shell answers each result are
`//meta/scripts:test_ci_enforcement_manifest`'s, which holds every required check and fan-in in
every workflow. What stays here is what that file cannot see: which of this workflow's jobs is the
gate, and the cost that picks `!cancelled()` over `always()`.

The guard shell is run rather than matched: asserting on its spelling would fail an idiom that
behaves identically, and pass one that does not.
"""

import re
import subprocess
import unittest
from pathlib import Path

import yaml
from meta.scripts._workflows import job_condition, job_needs
from meta.scripts.classify_changed_paths import emitted_sets

# Not .resolve(): the workflow is a cross-package data dep, so it lives in the runfiles tree beside
# this file rather than at the source path a resolved symlink would lead back to.
_WORKFLOW = Path(__file__).parent.parent.parent / ".github/workflows/devcontainer.yml"
_JOBS = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))["jobs"]

# The guard step, by name. Its position is what makes it a guard: it runs before the job pays for
# an npm install and a container build it has been told not to trust.
_GUARD_STEP = "Verify the classification ran"

_CLASSIFIER = "classify_changed_paths.py"
_GATE_RE = re.compile(r"needs\.changes\.outputs\.(\w+)")
_STEP_OUTPUT_RE = re.compile(r"^\$\{\{\s*steps\.(\w+)\.outputs\.(\w+)\s*\}\}$")

# Every result a `needs:` job can report other than success. `skipped` is the one that matters:
# it is what a failed dependency and a false `if:` both produce, and what GitHub counts as a pass.
_NOT_SUCCESS = ("failure", "cancelled", "skipped")


def classify_step() -> dict:
    """The step that runs the classifier — the one the job's `outputs:` read back from."""
    for step in _JOBS["changes"]["steps"]:
        if _CLASSIFIER in step.get("run", ""):
            return step
    raise AssertionError(f"no step running the classifier in {_WORKFLOW.name}'s `changes` job")


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


class ClassificationOutputsTest(unittest.TestCase):
    """The three hops between a pattern set and a gated step, each silent when it breaks."""

    def setUp(self):
        self.emitted = emitted_sets(classify_step()["run"])
        self.outputs = _JOBS["changes"]["outputs"]

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
                # `fail` rather than `assertIsNotNone`: it is typed `NoReturn`, so the two reads
                # below are narrowed for ty as well as guarded at run time.
                if hit is None:
                    self.fail(f"`{name}` is not a plain step-output reference")
                self.assertEqual(hit.group(1), classify_step()["id"])
                self.assertEqual(hit.group(2), name)

    def test_every_gate_reads_an_output_the_job_declares(self):
        """A gated step is the one place a typo costs nothing at all: it just never runs."""
        gates = set(_GATE_RE.findall(_WORKFLOW.read_text(encoding="utf-8")))
        self.assertTrue(gates, f"nothing in {_WORKFLOW.name} gates on the classification")
        self.assertEqual(sorted(gates - set(self.outputs)), [])


class BaseImageFanInTest(unittest.TestCase):
    """`skipped` from the matrix is legitimate; `skipped` because the gate never ran is not."""

    def test_the_fan_in_depends_on_both_the_matrix_and_the_gate(self):
        """The manifest runs the fan-in against every edge it has; it cannot see one that is gone.

        Its exemption lets `base-image-all` accept a skipped matrix, and its strictness rules then
        hold the shell to every other `needs:` entry — so dropping `changes` from that list leaves
        nothing asking whether the gate ran, and every rule there still passes.
        """
        self.assertEqual(
            sorted(job_needs(_JOBS["base-image-all"])),
            ["base-image", "changes"],
            "a fan-in that does not depend on the gate cannot tell a matrix the gate skipped "
            "from one it never reached",
        )


class BuildAndSmokeTestGuardTest(unittest.TestCase):
    """The required check that is deleted, rather than failed, by a failed dependency."""

    def setUp(self):
        self.job = _JOBS["build-and-smoke-test"]

    def test_the_job_survives_a_failed_classification(self):
        """Narrower than the manifest, which accepts `always()` too on any required job.

        Which of the two a job carries is a cost decision the manifest cannot see: see
        .claude/CLAUDE.md "Superseding CI runs".
        """
        self.assertEqual(
            job_condition(self.job),
            "!cancelled()",
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
