"""Holds the commit-file-via-app self-test to the shape a required status check has to have.

`Action self-test` is the only gate between a change to `.github/actions/commit-file-via-app/` and
the repos outside this one that reference it at `@main`. The ruleset that requires it is a repo
setting nothing in the tree can read, so what this file can hold is everything that setting depends
on — each of which fails silently:

  - **A trigger-level `paths:` filter.** GitHub counts a job skipped by `if:` as passing, but a
    workflow skipped by path filtering never reports: the required check sits `Pending` and blocks
    every PR that does not touch the action. Re-adding the filter looks like a tidy-up and takes the
    merge queue down with it. See .claude/CLAUDE.md "A required check cannot be filtered at the
    trigger".
  - **The check's name.** It is a string in repo settings, so the couplings holdable here are
    between the job and the docs that quote it. Rename the job and the ruleset goes on requiring a
    context nothing produces — which, unlike the filter, wedges the merge with no red run to read.
  - **The classification.** Now that the trigger runs on every PR, the pattern set is the whole of
    what decides that the exercise runs at all. A set that stops matching this workflow's own path,
    or an `--emit` naming a set that no longer exists, ends with a green check that ran nothing.
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
import re
import subprocess
import tempfile
import unittest
from itertools import pairwise
from pathlib import Path

import yaml
from meta.scripts.path_classification_pattern_sets import SETS

# Not .resolve(): every file read here is a cross-package data dep, so each lives in the runfiles
# tree beside this one rather than at the source path a resolved symlink would lead back to.
_ROOT = Path(__file__).parent.parent.parent
_WORKFLOW_PATH = ".github/workflows/commit-file-via-app-selftest.yml"
_WORKFLOW = _ROOT / _WORKFLOW_PATH
_ACTION_PATH = ".github/actions/commit-file-via-app/action.yml"

# The context the ruleset requires, and the id of the job that produces it.
_CHECK_NAME = "Action self-test"
# The ref the checkout must use. `github.head_ref` is a bare branch name and checkout's
# `repository:` defaults to this repo, so on a fork PR it resolves the fork's branch name here.
_CHECKOUT_ACTION = "actions/checkout@"
_PULL_HEAD_REF = "refs/pull/"
_JOB = "selftest"
_DOCS_NAMING_THE_CHECK = (_ROOT / "README.md", _ROOT / ".claude" / "CLAUDE.md")

# The two steps that decide anything, and the two conditions a step after them may carry: the
# gate's own verdict, or the cleanup's, which reads the scratch step's outcome instead — `skipped`
# whenever the gate stood the exercise down.
_CLASSIFY_ID = "classify"
_GATE_ID = "gate"
_SET_NAME = "commit_file_via_app"
_GATE = f"steps.{_GATE_ID}.outputs.run == 'true'"
_CLEANUP_GATE = "steps.scratch.outcome == 'success'"


def workflow() -> dict:
    """The parsed workflow. `on:` is YAML 1.1's `true`, which is why nothing here spells it."""
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def triggers(parsed: dict) -> dict:
    """The `on:` mapping, under whichever key the loader produced for it."""
    for key in (True, "on"):
        if key in parsed:
            return parsed[key]
    raise AssertionError(f"no `on:` block in {_WORKFLOW.name}")


def job() -> dict:
    """The one job. Structural rather than a text slice: a step's `if:` is only distinguishable
    from a job's, or from a line inside a `run: |` block, by where it sits."""
    jobs = workflow()["jobs"]
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


def emitted_sets() -> list[str]:
    """The set names the classify step asks for, read off its `--emit` arguments."""
    for step in job()["steps"]:
        if step.get("id") == _CLASSIFY_ID:
            words = step["run"].replace("\\\n", " ").split()
            return [word for prev, word in pairwise(words) if prev == "--emit"]
    raise AssertionError(f"no step with `id: {_CLASSIFY_ID}` in the `{_JOB}` job")


class TestTheWorkflowAlwaysReports(unittest.TestCase):
    def test_the_trigger_carries_no_path_filter(self):
        for event, config in triggers(workflow()).items():
            for key in ("paths", "paths-ignore"):
                with self.subTest(event=event, key=key):
                    self.assertNotIn(
                        key,
                        config or {},
                        f"a `{key}:` filter skips the whole workflow, and a required check whose "
                        "workflow never runs stays Pending — it blocks every PR that does not "
                        "touch the action, instead of passing them",
                    )

    def test_it_runs_on_pull_requests_to_main(self):
        # The event the ruleset evaluates against. A workflow that reports only on pushes would
        # leave the same Pending check as a path filter.
        self.assertIn("pull_request", triggers(workflow()))
        self.assertEqual(triggers(workflow())["pull_request"]["branches"], ["main"])


class TestTheCheckName(unittest.TestCase):
    def test_the_job_is_named_what_branch_protection_names(self):
        self.assertEqual(job()["name"], _CHECK_NAME)

    def test_docs_name_the_check(self):
        # The ruleset itself is unreadable from here, so the docs that quote the string are the
        # only copies a rename can be held against.
        for doc in _DOCS_NAMING_THE_CHECK:
            with self.subTest(doc=doc.name):
                self.assertIn(_CHECK_NAME, doc.read_text(encoding="utf-8"))


class TestTheClassification(unittest.TestCase):
    def test_the_emitted_sets_exist(self):
        # `select()` refuses an unknown name, so a renamed set fails the step rather than emitting
        # `false` forever — but it fails on a PR, at which point the rename is already written.
        self.assertEqual(emitted_sets(), [_SET_NAME])
        self.assertIn(_SET_NAME, SETS)

    def test_the_set_matches_what_the_self_test_covers(self):
        # Both halves rename easily and neither rename fails anything: the workflow could be moved
        # and the action's directory could be, and the set would go on matching nothing. So each
        # path is asserted to exist as well as to match — a constant matching a stale pattern is
        # the one way both sides drift together and stay green.
        for path in (_WORKFLOW_PATH, _ACTION_PATH):
            with self.subTest(path=path):
                self.assertTrue(
                    (_ROOT / path).is_file(),
                    f"{path} does not exist; the constant and the pattern can drift together",
                )
                self.assertTrue(
                    any(re.search(pattern, path) for pattern in SETS[_SET_NAME]),
                    f"{path} is not in the `{_SET_NAME}` set, so a PR touching it would skip the "
                    "self-test and report success",
                )

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

    The job carries no fork `if:` any more. That shape skipped the whole job, and branch protection
    counts a skipped required check as a pass — so a fork could change the action and report green
    having verified nothing, on the one check standing between this action and the repos that
    consume it at `@main`."""

    def test_the_job_never_skips_itself(self):
        self.assertNotIn(
            "if",
            job(),
            "a job-level `if:` skips the whole job, and skipped reads as passed — whatever the "
            "condition, the refusal has to be a step that fails",
        )

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
