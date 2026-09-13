"""Holds the commit-file-via-app self-test to the shape a required status check has to have.

`Action self-test` is the only gate between a change to `.github/actions/commit-file-via-app/` and
the repos outside this one that reference it at `@main`. Requiring it in the ruleset is a repo
setting nothing in the tree can read, so what this file can hold is everything the setting depends
on — and each of those fails silently, which is why they are worth a test at all:

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
  - **Where the classification sits.** In a step of the required job, not a job the rest `needs:`.
    A failed dependency skips its dependents, and a skipped required check reads as a pass, so the
    `needs:` shape would turn a broken classifier into a green gate.

What the self-test *asserts* about the action is the workflow's own business and is not read here.
"""

import re
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

# The context branch protection requires, and the id of the job that produces it.
_CHECK_NAME = "Action self-test"
_JOB = "selftest"
_DOCS_NAMING_THE_CHECK = (_ROOT / "README.md", _ROOT / ".claude" / "CLAUDE.md")

# The classify step, and the three conditions a step following it is allowed to carry: the gate
# itself, its negation (the leg that says the self-test was skipped), and the cleanup, which reads
# the scratch step's outcome instead — `skipped` whenever the classification skipped it.
_CLASSIFY_ID = "classify"
_SET_NAME = "commit_file_via_app"
_OUTPUT = f"steps.{_CLASSIFY_ID}.outputs.{_SET_NAME}"
_GATE = f"{_OUTPUT} == 'true'"
_SKIPPED_LEG = f"{_OUTPUT} != 'true'"
_CLEANUP_GATE = "steps.scratch.outcome == 'success'"

# Fork PRs cannot read the app credentials, so the job skips there — an accepted gap, spelled out
# at the condition itself. Held because dropping it turns every fork PR red on a check none of them
# can run, and widening it is how the credentials would reach unreviewed code.
_FORK_SKIP = "github.event.pull_request.head.repo.full_name == github.repository"


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


def steps_after_classify() -> list[dict]:
    """Every step following the classify step, which is every step that could need gating."""
    steps = job()["steps"]
    for index, step in enumerate(steps):
        if step.get("id") == _CLASSIFY_ID:
            return steps[index + 1 :]
    raise AssertionError(f"no step with `id: {_CLASSIFY_ID}` in the `{_JOB}` job")


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
        # and the action's directory could be, and the set would go on matching nothing.
        for path in (_WORKFLOW_PATH, _ACTION_PATH):
            with self.subTest(path=path):
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
        for step in steps_after_classify():
            name = step.get("name") or step.get("uses") or step.get("run", "")[:40]
            with self.subTest(step=name):
                condition = step.get("if", "")
                self.assertTrue(
                    any(g in condition for g in (_GATE, _SKIPPED_LEG, _CLEANUP_GATE)),
                    f"step `{name}` runs on every PR; it should be gated on the classification",
                )

    def test_the_unchanged_leg_reports_what_happened(self):
        # Without it a PR that touched nothing shows a job of skipped steps and no statement that
        # skipping them was the right answer.
        legs = [s for s in steps_after_classify() if _SKIPPED_LEG in s.get("if", "")]
        self.assertEqual(len(legs), 1)


class TestTheForkGap(unittest.TestCase):
    def test_the_job_skips_fork_pull_requests(self):
        self.assertIn(_FORK_SKIP, job()["if"])


if __name__ == "__main__":
    unittest.main()
