"""Holds ci.yml's expensive Bazel jobs behind the checks that say a derived file is stale.

Scheduling, not enforcement. `needs:` decides what *runs* and the ruleset decides what must *pass*
— .claude/CLAUDE.md "Enforcement lives in the ruleset, not in `needs`" — so nothing here makes a
check binding, and the closure rule that keeps a required job from depending on a non-required one
is `//meta/scripts:test_ci_enforcement_manifest`'s. What this file adds is the one scheduling
property that is worth asserting rather than leaving to whoever edits the graph next.

The property: **a job that pays for a stale derived file many times over does not start until the
check that reads that file has reported.** Renovate is what makes the case concrete. Mend-hosted
Renovate cannot run `uv lock`, `bazel mod deps` or `go mod tidy`, so it pushes bumped manifests
with the derived files left behind and `renovate-derived-files.yml` commits the regenerated copies
back a minute or two later. Every workflow fires on the first push, against a tree whose derived
files are legitimately stale.

What makes that worth ordering is that the builds do not notice. `--lockfile_mode=update` rewrites
`MODULE.bazel.lock` in memory and stays green, so three platform runners plus coverage spend
minutes each confirming an answer nobody doubted, on an input a cheap job has already called
stale. The ordering buys runner time and a quieter failure history; it does not change any
verdict, and it does not make the race against the helper's commit winnable.

What the gate reports when it fires is worth being exact about, because a failed upstream leaves
its dependents `skipped` and branch protection counts `skipped` as a pass — .claude/CLAUDE.md "A
required check cannot be filtered at the trigger". For the matrix that is answered: `build and
test (all targets)` is the strict fan-in that refuses `skipped` in its place, and that `always()`
and that strictness are `//meta/scripts:test_ci_enforcement_manifest`'s. `Coverage` has no fan-in
and so does go green on a skip; what blocks the merge there is the freshness check's own failure.
That is the shape every `needs:` edge on a required job has, and this file adds an edge rather
than the shape.

Who is behind the gate and who is beside it is a cost judgement, which is why both halves are
recorded with reasons rather than derived from a rule. A Bazel job added without an entry fails
`AccountingTest` — the same default-to-visible posture the enforcement manifest takes, one axis
over.
"""

import re
import unittest
from pathlib import Path

import yaml
from meta.scripts._workflows import job_needs

# Not .resolve(): the workflow is a cross-package data dep, so it lives in the runfiles tree beside
# this file rather than at the source path a resolved symlink would lead back to.
_WORKFLOW = Path(__file__).parent.parent.parent / ".github/workflows/ci.yml"
_JOBS = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))["jobs"]

# Two signals, unioned rather than picked between, because each misses a job the other catches: a
# Bazel invocation that needs no remote cache, and a cache setup whose Bazel call is spelled some
# other way. Over-detection costs an entry in one of the lists below; under-detection costs the
# gate, silently, for exactly the job that was added without thinking about it.
_BAZEL_RE = re.compile(r"\bbazel\b")
_BAZEL_SETUP = "setup-bazel-remote"

# The jobs that read a derived file and fail when it is not what its generator would produce. Both
# are required, which is what lets a required job depend on them at all. The value names the file,
# because "freshness check" is only meaningful if you can say freshness of what.
_FRESHNESS_JOBS = {
    "bazel-lock-check": "MODULE.bazel and MODULE.bazel.lock, against `bazel mod tidy`",
    "modules-check": "requirements_lock.txt, against uv.lock",
}

# Must sit behind every freshness job, transitively. The reason is always cost: a build that runs
# per platform, or a measurement downstream of one.
_GATED = {
    "build-and-test-per-target": (
        "Three platform runners per PR, each a full `bazel test //...`. The multiplier is the "
        "whole argument — one cheap job already knows the answer is being computed over a stale "
        "input, and `--lockfile_mode=update` means the build will not say so."
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
        "One runner, and roughly as cheap as the freshness checks themselves. Serialising it "
        "behind one of them would delay `build-and-test-per-target` by their sum instead of "
        "their maximum, since it waits on gazelle too — so the gate would cost the common green "
        "PR more than the stale-lock PR saves. A stale `use_repo` can move gazelle's answer, and "
        "when it does both jobs go red together, which is the clearer report anyway."
    ),
}


def bazel_jobs() -> dict[str, dict]:
    """Every job in ci.yml that invokes Bazel, and so reads MODULE.bazel.lock."""
    found = {}
    for job_id, body in _JOBS.items():
        steps = body.get("steps") or []
        if any(_BAZEL_RE.search(step.get("run", "") or "") for step in steps) or any(
            _BAZEL_SETUP in (step.get("uses", "") or "") for step in steps
        ):
            found[job_id] = body
    return found


def upstream_of(job_id: str) -> set[str]:
    """Every job reachable from `job_id` through `needs:`, however many hops away.

    Transitive rather than direct on purpose: `coverage` is gated through `build-and-test-all`,
    and an edge added to shorten a chain is a scheduling change this file has no business failing.
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
