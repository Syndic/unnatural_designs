"""Holds ci.yml's expensive Bazel jobs behind the checks that say a derived file is stale.

Scheduling, not enforcement: why the gate exists, what it costs and what it reports when it fires
are .claude/CLAUDE.md's, under "Enforcement lives in the ruleset, not in `needs`". This file holds
the derived-file half of it — every gated job waits, directly or through another job, for every
freshness check — and accounts for every Bazel job in both directions, so one added later arrives
classified rather than ungated and an entry for a job that stopped running Bazel is flagged.

Detection reads the workflow text, so a job that reaches Bazel only through a script it calls is
invisible here. Over-detection costs an entry in one of the lists below; under-detection costs the
gate, silently, for exactly the job that was added without thinking about it — so both signals
are unioned and each is matched broadly.
"""

import re
import unittest
from pathlib import Path

from meta.scripts._workflows import job_needs, step_containers

# Left unresolved because the workflow is a cross-package data dep, so it lives in the runfiles tree
# beside this file rather than at the source path a resolved symlink would lead back to.
_WORKFLOW = Path(__file__).parent.parent.parent / ".github/workflows/ci.yml"
_CONTAINERS = step_containers(_WORKFLOW)
_JOBS = {container.label: container.scope for container in _CONTAINERS}

# A `bazel` or `bazelisk` call in a `run:`, or any setup-bazel action — the repo's own
# `setup-bazel-remote` and the upstream `bazel-contrib/setup-bazel` both match.
_BAZEL_RE = re.compile(r"\bbazel(isk)?\b")
_BAZEL_SETUP = "setup-bazel"

# The jobs that read a derived file and fail when it is not what its generator would produce. Both
# are required, which is what lets a required job depend on them at all. The value names the file,
# because "freshness check" is only meaningful if you can say freshness of what.
_FRESHNESS_JOBS = {
    "bazel-lock-check": "MODULE.bazel and MODULE.bazel.lock, against `bazel mod tidy`",
    "modules-check": "requirements_lock.txt, against uv.lock",
}

# Must sit behind every freshness job, transitively. The reason is always cost: a full Bazel build,
# paid once per platform or once more for coverage.
_GATED = {
    "build-and-test-per-target": (
        "Three platform runners per PR, each a full `bazel test //...`, spent on a commit a cheap "
        "required check has already ruled unmergeable. The multiplier is the whole argument."
    ),
    "coverage": (
        "A fourth full build, and `bazel coverage //...` is slower than the test run it repeats. "
        "Shares the platform builds' anchored `needs:` list rather than carrying an edge of its "
        "own, so the gate reaches it by alias and the two cannot drift apart."
    ),
}

# Bazel jobs deliberately beside the gate rather than behind it, one reason each.
_AHEAD_OF_THE_GATE = {
    "gazelle-check": (
        "The gate's long pole: a minute or more, where the lock check takes well under one. "
        "Behind the lock check it would add that check's time to every green PR; beside it, the "
        "lock check finishes inside gazelle's run and the gate costs a green PR nothing."
    ),
}


def invokes_bazel(step: dict) -> bool:
    """Whether one step runs Bazel or sets it up, by either signal."""
    return bool(_BAZEL_RE.search(step.get("run") or "")) or _BAZEL_SETUP in (step.get("uses") or "")


def bazel_jobs() -> set[str]:
    """Every job in ci.yml that invokes Bazel, and so reads MODULE.bazel.lock."""
    return {
        container.label
        for container in _CONTAINERS
        if any(isinstance(step, dict) and invokes_bazel(step) for step in container.steps)
    }


def upstream_of(job_id: str) -> set[str]:
    """Every job reachable from `job_id` through `needs:`, however many hops away.

    Transitive rather than direct on purpose: an edge that reaches a job through another job's
    `needs:` gates it just as well, and lengthening or shortening a chain is a scheduling change
    this file has no business failing.
    """
    seen: set[str] = set()
    pending = list(job_needs(_JOBS[job_id]))
    while pending:
        needed = pending.pop()
        if needed in seen:
            continue
        seen.add(needed)
        pending.extend(job_needs(_JOBS[needed]))
    return seen


class VacuityTest(unittest.TestCase):
    """Every assertion below iterates something, and an empty something passes them all."""

    def test_the_workflow_parsed(self):
        self.assertTrue(_JOBS, f"no jobs in {_WORKFLOW.name}")

    def test_bazel_jobs_were_found(self):
        self.assertTrue(bazel_jobs(), f"no job in {_WORKFLOW.name} was detected as using Bazel")

    def test_the_lists_are_populated(self):
        self.assertTrue(_FRESHNESS_JOBS, "no freshness check to gate on")
        self.assertTrue(_GATED, "no job is gated, so the gating assertion asks nothing")


class NamesTest(unittest.TestCase):
    """A renamed job leaves an entry naming nothing, and an entry naming nothing asserts nothing."""

    def test_every_named_job_exists(self):
        named = set(_FRESHNESS_JOBS) | set(_GATED) | set(_AHEAD_OF_THE_GATE)
        for job_id in sorted(named):
            with self.subTest(job=job_id):
                self.assertIn(
                    job_id,
                    _JOBS,
                    f"`{job_id}` is classified here but is not a job in {_WORKFLOW.name}. If it "
                    "was renamed, this file is now silent about the job that replaced it.",
                )

    def test_every_entry_records_a_reason(self):
        for label, entries in (("gated", _GATED), ("ahead of the gate", _AHEAD_OF_THE_GATE)):
            for job_id, reason in sorted(entries.items()):
                with self.subTest(job=job_id, list=label):
                    self.assertTrue(
                        reason.strip(),
                        f"`{job_id}` is recorded as {label} with no reason, which is the omission "
                        "these lists exist to turn into a decision",
                    )


class AccountingTest(unittest.TestCase):
    """A Bazel job arrives classified or it fails here. Unclassified would mean ungated."""

    def test_every_bazel_job_is_classified_exactly_once(self):
        lists = (
            ("a freshness check", _FRESHNESS_JOBS),
            ("gated", _GATED),
            ("ahead of the gate", _AHEAD_OF_THE_GATE),
        )
        for job_id in sorted(bazel_jobs()):
            listed = [label for label, entries in lists if job_id in entries]
            with self.subTest(job=job_id):
                self.assertEqual(
                    len(listed),
                    1,
                    f"`{job_id}` invokes Bazel and is {listed or 'in no list here'}. Put it in "
                    "exactly one: behind the gate if it pays for a stale derived file more than "
                    "the freshness checks cost, beside it with the reason if not.",
                )

    def test_every_classified_job_still_invokes_bazel(self):
        """The other direction: an entry for a job that stopped running Bazel asserts nothing."""
        detected = bazel_jobs()
        for label, entries in (("gated", _GATED), ("ahead of the gate", _AHEAD_OF_THE_GATE)):
            for job_id in sorted(entries):
                with self.subTest(job=job_id, list=label):
                    self.assertIn(
                        job_id,
                        detected,
                        f"`{job_id}` is recorded as {label}, but no longer invokes Bazel by either "
                        "signal. Remove the entry — or, if it reaches Bazel some way this file "
                        "cannot see, teach `invokes_bazel` rather than keeping a stale entry.",
                    )

    def test_no_freshness_check_is_gated_on_itself(self):
        """A gate behind the gate never reports, and a check that never reports blocks every PR."""
        for job_id in sorted(_FRESHNESS_JOBS):
            with self.subTest(job=job_id):
                self.assertNotIn(job_id, _GATED, f"`{job_id}` is the gate; it cannot be behind it")


class GatingTest(unittest.TestCase):
    """The assertion the file exists for."""

    def test_every_gated_job_waits_for_every_freshness_check(self):
        for job_id in sorted(_GATED):
            upstream = upstream_of(job_id)
            for gate, derived in sorted(_FRESHNESS_JOBS.items()):
                with self.subTest(job=job_id, gate=gate):
                    self.assertIn(
                        gate,
                        upstream,
                        f"`{gate}` reads {derived}, and `{job_id}` reads the same file without "
                        f"waiting for it — so it runs against an input `{gate}` has already "
                        f"called stale. Recorded cost of doing that: {_GATED[job_id]}",
                    )

    def test_no_freshness_check_waits_for_a_gated_job(self):
        """The cycle GitHub would reject, caught with the reason rather than as a parse error."""
        for gate in sorted(_FRESHNESS_JOBS):
            upstream = upstream_of(gate)
            for job_id in sorted(_GATED):
                with self.subTest(gate=gate, job=job_id):
                    self.assertNotIn(job_id, upstream, f"`{gate}` cannot wait for `{job_id}`")


if __name__ == "__main__":
    unittest.main()
