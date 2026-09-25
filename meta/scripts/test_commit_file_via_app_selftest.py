"""Holds the commit-file-via-app self-test to what its required status check depends on.

`Action self-test` is the only gate between a change to `.github/actions/commit-file-via-app/` and
the repos outside this one that reference it at `@main`. What every required check needs — its
name, a trigger with no path filter, no job-level gate — is
`//meta/scripts:test_ci_enforcement_manifest`'s, and which paths the set matches is
`:test_path_classification_pattern_sets`'s. What is held here is particular to this workflow, and
each piece fails silently:

  - **The classification reaching the gate.** The trigger runs on every PR, so the classification
    is the whole of what decides that the exercise runs. The step has to emit the set the gate
    reads; a name that agrees with nothing is `false` on every run.
  - **Where the classification sits.** In a step of the reporting job, not a job the rest `needs:`.
    A failed dependency skips its dependents, and a skipped required check reads as a pass, so the
    `needs:` shape would turn a broken classifier into a green gate.
  - **What happens on a fork PR.** A fork cannot read the app credentials, so it cannot run the
    exercise — and the rule is that it therefore cannot propose the change either. The job used to
    carry a fork `if:` and skip, which reads as a pass, so a fork could change the action and
    report green having verified nothing. The refusal lives in the gate step's shell, which these
    tests run rather than read: a `skipped` and a `failure` are one character apart in YAML and
    opposite in meaning.

What the self-test *asserts* about the action is the workflow's own business and is not read here.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml
from meta.scripts.classify_changed_paths import emitted_sets

# Not .resolve(): every file read here is a cross-package data dep, so each lives in the runfiles
# tree beside this one rather than at the source path a resolved symlink would lead back to.
_ROOT = Path(__file__).parent.parent.parent
_WORKFLOW = _ROOT / ".github/workflows/commit-file-via-app-selftest.yml"
# README's copy of the check name is the manifest test's; CLAUDE.md's is only held here.
_CLAUDE_MD = _ROOT / ".claude" / "CLAUDE.md"

# The ref the checkout must use. `github.head_ref` is a bare branch name and checkout's
# `repository:` defaults to this repo, so on a fork PR it resolves the fork's branch name here.
_CHECKOUT_ACTION = "actions/checkout@"
_PULL_HEAD_REF = "refs/pull/"
_JOB = "selftest"

# The two steps that decide anything, and the two conditions a step after them may carry: the
# gate's own verdict, or the cleanup's, which reads the scratch step's outcome instead — `skipped`
# whenever the gate stood the exercise down.
_CLASSIFY_ID = "classify"
_GATE_ID = "gate"
_SET_NAME = "commit_file_via_app"
_GATE = f"steps.{_GATE_ID}.outputs.run == 'true'"
_CLEANUP_GATE = "steps.scratch.outcome == 'success'"


def job() -> dict:
    """The one job. Structural rather than a text slice: a step's `if:` is only distinguishable
    from a job's, or from a line inside a `run: |` block, by where it sits."""
    jobs = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    if _JOB not in jobs:
        raise AssertionError(f"no `{_JOB}:` job in {_WORKFLOW.name}")
    return jobs[_JOB]


def step_with_id(step_id: str) -> dict:
    """The one step carrying `step_id`."""
    for step in job()["steps"]:
        if step.get("id") == step_id:
            return step
    raise AssertionError(f"no step with `id: {step_id}` in the `{_JOB}` job")


def steps_after_gate() -> list[dict]:
    """Every step following the gate, which is every step that could need gating."""
    steps = job()["steps"]
    for index, step in enumerate(steps):
        if step.get("id") == _GATE_ID:
            return steps[index + 1 :]
    raise AssertionError(f"no step with `id: {_GATE_ID}` in the `{_JOB}` job")


def run_gate(changed: str, from_fork: str) -> tuple[int, str]:
    """Run the gate step's real shell and return `(exit status, what it wrote to GITHUB_OUTPUT)`.

    Executed rather than read. The difference between refusing a fork and quietly standing the
    exercise down is one `exit` in a shell branch, and every way of asserting that by pattern
    passes on a script that does the opposite."""
    script = step_with_id(_GATE_ID)["run"]
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "github_output"
        output.touch()
        done = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "CHANGED": changed,
                "FROM_FORK": from_fork,
                "GITHUB_OUTPUT": str(output),
            },
        )
        return done.returncode, output.read_text(encoding="utf-8")


class TestTheCheckName(unittest.TestCase):
    def test_claude_md_names_the_check(self):
        """Read off the job, so a rename the manifest and README follow cannot leave this behind."""
        self.assertIn(job()["name"], _CLAUDE_MD.read_text(encoding="utf-8"))


class TestTheClassification(unittest.TestCase):
    def test_the_step_emits_the_set_the_gate_reads(self):
        # Whether the name exists at all is :test_classify_changed_paths_callers'. A step emitting
        # a real set the gate does not read would pass that and stand the exercise down forever.
        self.assertEqual(emitted_sets(step_with_id(_CLASSIFY_ID)["run"]), [_SET_NAME])

    def test_the_classification_is_a_step_not_a_dependency(self):
        # A `changes` job the rest `needs:` would report `skipped` on failure, and branch
        # protection counts a skipped required check as passed.
        self.assertNotIn("needs", job())


class TestEveryExercisingStepIsGated(unittest.TestCase):
    def test_no_step_reaches_the_app_credentials_ungated(self):
        for step in steps_after_gate():
            name = step.get("name") or step.get("uses") or step.get("run", "")[:40]
            with self.subTest(step=name):
                condition = step.get("if", "")
                self.assertTrue(
                    any(g in condition for g in (_GATE, _CLEANUP_GATE)),
                    f"step `{name}` runs on every PR; it should be gated on the gate's verdict",
                )


class TestTheCheckout(unittest.TestCase):
    """The job stopped restricting itself to same-repo PRs, which made the checkout's ref wrong.

    `github.head_ref` carries the branch *name* with no owner, and `repository:` defaults to this
    repository — so a fork's branch name was resolved against this repo. A name with no counterpart
    here failed the job before the gate could refuse anything; a name that collided took this
    repo's branch of that name instead. The second is the dangerous one: a fork whose branch is
    `main` checked out this repo's `main`, so the classifier diffed main against main, found
    nothing, and the check went green over a change to the action. That is the hole the fork rule
    exists to close, reached through the commonest fork-PR shape there is."""

    def setUp(self):
        self.checkout = next(s for s in job()["steps"] if _CHECKOUT_ACTION in (s.get("uses") or ""))

    def test_the_ref_resolves_in_this_repository(self):
        ref = self.checkout["with"]["ref"]
        self.assertIn(
            _PULL_HEAD_REF,
            ref,
            "the ref must be one this repository actually has for a fork PR; GitHub maintains "
            "refs/pull/N/head for both fork and same-repo PRs",
        )
        self.assertIn("pull_request.number", ref)

    def test_the_ref_is_the_head_not_the_merge_commit(self):
        # refs/pull/N/merge would also resolve, and would change what the scratch branch is
        # created at and what the exercise diffs — `git rev-parse HEAD` is read downstream.
        self.assertNotIn("/merge", self.checkout["with"]["ref"])

    def test_no_step_resolves_a_fork_branch_name_against_this_repo(self):
        # The regression guard. `github.head_ref` is correct only while the job refuses to run on
        # fork PRs, and it no longer does.
        for step in job()["steps"]:
            name = step.get("name") or step.get("uses") or ""
            with self.subTest(step=name):
                self.assertNotIn("github.head_ref", str(step.get("with", "")))


class TestTheForkRule(unittest.TestCase):
    """A fork cannot run the exercise, so it cannot propose the change the exercise covers.

    The refusal is a step that fails, never a job-level `if:` that skips: branch protection counts
    a skipped required check as a pass, so a fork could change the action and report green having
    verified nothing. The manifest test refuses a falsifiable `if:` on any required job."""

    def test_a_fork_touching_the_action_is_refused(self):
        status, _ = run_gate(changed="true", from_fork="true")
        self.assertNotEqual(
            status,
            0,
            "a fork PR cannot run the exercise, so a change to the action must fail here rather "
            "than pass unverified",
        )

    def test_a_fork_touching_nothing_else_still_passes(self):
        # Forks are refused from one change, not blocked in general.
        status, outputs = run_gate(changed="false", from_fork="true")
        self.assertEqual(status, 0)
        self.assertIn("run=false", outputs)

    def test_the_exercise_runs_for_this_repo_when_the_action_changed(self):
        status, outputs = run_gate(changed="true", from_fork="false")
        self.assertEqual(status, 0)
        self.assertIn("run=true", outputs)

    def test_nothing_to_exercise_stands_down_rather_than_failing(self):
        status, outputs = run_gate(changed="false", from_fork="false")
        self.assertEqual(status, 0)
        self.assertIn("run=false", outputs)

    def test_the_gate_reads_both_questions_from_the_workflow(self):
        # The shell above is only as good as what the workflow binds into it: a missing `env:` key
        # is an unset variable, which `set -u` turns into a failed job on every PR.
        env = step_with_id(_GATE_ID)["env"]
        self.assertIn(_SET_NAME, env["CHANGED"])
        self.assertIn("head.repo.full_name != github.repository", env["FROM_FORK"])


if __name__ == "__main__":
    unittest.main()
