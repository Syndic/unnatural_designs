"""Holds every `pull_request` job to the enforcement manifest, and the manifest to the workflows.

`ci_enforcement_manifest.py` is a claim about repo settings. This file is what makes the claim do
work: it fails a job nobody classified, an edge that moved enforcement without saying so, a
required check that cannot report, a matrix nothing can require, and a fan-in that accepts a result
it should refuse.

The properties, and why each one is not implied by the others:

  - **Accounting.** Every job is required, not-required, or a matrix job. Without it a new job
    enforces nothing by default and silently, which is the whole defect.
  - **The closure rule.** A blocking job requires only blocking jobs. Accounting says each job was
    classified; this says a `needs:` edit did not quietly overrule the classification, in either
    direction — a required check unhooked to parallelise it, or a non-required job pulled into a
    required job's `needs` so its failure now takes a merge gate down.
  - **Trigger reach.** A required check's workflow runs on every PR. A workflow filtered at its
    trigger never reports, and a required check that never reports sits `Pending` forever — the
    opposite failure to a skipped job, which reads as a pass.
  - **Matrix shape.** Every matrix job has exactly one fan-in and at least one row. The fan-in is
    the only name a ruleset can require on a matrix's behalf; the row count is what stops an empty
    matrix from reporting `skipped` into a fan-in that accepts `skipped`.
  - **Fan-in strictness.** Requiring a fan-in means something only if it refuses what its rows
    report. Without this the manifest proves a check is *reachable* from a required name, not that
    reaching it has any consequence — and "reachable but inconsequential" was #310.

What none of it can do is read the ruleset, so none of it can tell you the manifest is true. That
gap is stated at the top of the manifest and repeated in the failure message an unclassified job
gets, because a green run here is the moment someone is most likely to assume otherwise. #314.

Scope is every workflow with `pull_request` in `on:`, discovered by glob. Jobs elsewhere — a
`schedule`-only workflow, say — cannot hold a PR merge and are not this file's business.
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path
from typing import NamedTuple

import yaml
from meta.scripts.ci_enforcement_manifest import (
    FAN_IN_EXEMPTIONS,
    NOT_REQUIRED,
    REQUIRED,
    FanInExemption,
)

# Not .resolve(): the workflows and README are cross-package data deps, so they live in the
# runfiles tree beside this file rather than at the source path a resolved symlink leads back to.
_ROOT = Path(__file__).parent.parent.parent
_WORKFLOW_DIR = _ROOT / ".github" / "workflows"
_README = _ROOT / "README.md"

_PULL_REQUEST = "pull_request"

# Every result a `needs:` job can report other than success. `skipped` is the one that matters: it
# is what a failed dependency and a false `if:` both leave behind, and what GitHub counts as a pass.
_NOT_SUCCESS = ("failure", "cancelled", "skipped")

# GitHub treats these as the same condition, and a fan-in written either way behaves identically.
_ALWAYS = ("always()", "${{ always() }}")

# Matrix sections rather than axes: neither is a dimension of the cross product.
_INCLUDE = "include"
_EXCLUDE = "exclude"


def _load_workflows() -> dict[str, dict]:
    """Every workflow in the tree, by filename.

    Globbed rather than listed, so a workflow added tomorrow is in scope with no edit here — which
    is exactly when a job is most likely to arrive unclassified. `//:workflows` globs for the same
    reason; a curated `data` list would leave a new workflow unread in the sandbox while CI ran it,
    which is the fail-open shape this file exists to remove.
    """
    return {
        path.name: yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in sorted(_WORKFLOW_DIR.glob("*.y*ml"))
    }


def triggers(workflow: dict) -> dict:
    """A workflow's `on:` block, as `{event: filters}`.

    `yaml.safe_load` reads a bare `on:` key as the boolean True — YAML 1.1's spelling of it — so
    the literal string is a fallback, not the common case. The list and scalar forms carry no
    filters and flatten to None values.
    """
    declared = workflow.get(True, workflow.get("on"))
    if isinstance(declared, str):
        return {declared: None}
    if isinstance(declared, list):
        return dict.fromkeys(declared)
    return declared or {}


class Job(NamedTuple):
    """One workflow job, carrying what it takes to classify it."""

    workflow: str
    job_id: str
    # What GitHub reports this job as, which is what a ruleset can name. For a matrix job it still
    # holds the un-interpolated `${{ matrix.x }}`, which is the point: there is no one name.
    check: str
    needs: tuple[str, ...]
    body: dict

    @property
    def is_matrix(self) -> bool:
        return "matrix" in (self.body.get("strategy") or {})

    def __str__(self) -> str:
        return f"{self.workflow}:{self.job_id}"


def _needs(body: dict) -> tuple[str, ...]:
    """A job's `needs:`. GitHub takes a bare scalar too, which `tuple()` would shred into chars."""
    declared = body.get("needs", [])
    return (declared,) if isinstance(declared, str) else tuple(declared)


def _pull_request_jobs() -> list[Job]:
    """Every job in every workflow that runs on `pull_request`.

    Membership is decided by the trigger and never by evaluating an `if:`. Three jobs here never
    run on an ordinary PR — `publish`, `re-derive`, `request-run` — and are in scope anyway.
    Approximating GitHub's expression semantics would drop jobs from the domain, and a job dropped
    from the domain is one this file passes without checking. The cost is three NOT_REQUIRED
    entries; what they buy is that "never runs on a PR" is written down somewhere at all.
    """
    found = []
    for name, workflow in sorted(_WORKFLOWS.items()):
        if _PULL_REQUEST not in triggers(workflow):
            continue
        for job_id, body in (workflow.get("jobs") or {}).items():
            found.append(Job(name, job_id, body.get("name") or job_id, _needs(body), body))
    return found


_WORKFLOWS = _load_workflows()
_JOBS = _pull_request_jobs()
_BY_ID = {(job.workflow, job.job_id): job for job in _JOBS}


def fan_ins_of(job: Job) -> list[Job]:
    """The non-matrix jobs in the same workflow that depend on `job`.

    A matrix job's fan-in is derived, not declared: it is whatever non-matrix job aggregates its
    rows. Fewer than one is a matrix nothing can require; more than one is ambiguous. Both are
    failures rather than a choice made quietly here.
    """
    return [
        other
        for other in _JOBS
        if other.workflow == job.workflow and not other.is_matrix and job.job_id in other.needs
    ]


def is_blocking(job: Job) -> bool:
    """Whether this job's failure blocks a merge.

    A non-matrix job answers for itself, by check name. A matrix job has no nameable check, so it
    inherits from its fan-in — which is non-matrix by construction, so this recurs exactly once.
    """
    if not job.is_matrix:
        return job.check in REQUIRED
    fan_ins = fan_ins_of(job)
    return len(fan_ins) == 1 and is_blocking(fan_ins[0])


def blocking_fan_ins() -> list[Job]:
    """Every fan-in that sits on a required path, deduplicated and in a stable order."""
    found = {}
    for job in _JOBS:
        if not job.is_matrix or not is_blocking(job):
            continue
        for fan_in in fan_ins_of(job):
            found[(fan_in.workflow, fan_in.job_id)] = fan_in
    return [found[key] for key in sorted(found)]


class Uncountable(Exception):
    """A matrix whose row count cannot be established from the tree."""


def matrix_rows(job: Job) -> int:
    """How many rows a matrix declares, or raise `Uncountable`.

    Only "is it at least one" is asked of the result, so this is a floor rather than GitHub's exact
    cross product — `include:` entries that match an existing combination augment it instead of
    adding a row, and a floor does not care which happened. What matters is that an unreadable
    matrix raises rather than returning a number: an empty matrix produces zero rows, the job
    reports `skipped`, and `base-image-all` accepts `skipped`, so a required check would go green
    having run nothing.
    """
    matrix = job.body["strategy"]["matrix"]
    if not isinstance(matrix, dict):
        raise Uncountable("`matrix:` is a computed expression, so its rows cannot be counted here")
    if _EXCLUDE in matrix:
        raise Uncountable(
            "`exclude:` can empty an axis and is not modelled here. No matrix in the tree uses "
            "one, so teach this function rather than widening the gate to accept it"
        )

    axes = {name: values for name, values in matrix.items() if name != _INCLUDE}
    for name, values in axes.items():
        if not isinstance(values, list):
            raise Uncountable(f"axis `{name}` is not a list, so its rows cannot be counted here")
    if axes:
        rows = 1
        for values in axes.values():
            rows *= len(values)
        return rows

    included = matrix.get(_INCLUDE)
    if not isinstance(included, list):
        raise Uncountable("`include:` is not a list, so its rows cannot be counted here")
    return len(included)


def run_fan_in(job: Job, results: dict[str, str]) -> subprocess.CompletedProcess:
    """Run a fan-in's real shell with each `needs.<job>.result` replaced by a given outcome.

    Executed rather than pattern-matched, for the reason //meta/scripts:test_codeql_toolchain
    gives: asserting on the spelling would fail an idiom that behaves identically and pass one that
    does not. The tree holds two idioms — four `if` fan-ins and `base-image-all`'s `case` — and
    neither is privileged here.
    """
    scripts = [step["run"] for step in job.body.get("steps", []) if "run" in step]
    if len(scripts) != 1:
        raise AssertionError(f"{job} has {len(scripts)} `run:` steps; which one is the fan-in?")

    script = scripts[0]
    for needed, result in results.items():
        # Concatenated rather than formatted: every brace in the expression is literal.
        expression = "${{ needs." + needed + ".result }}"
        # Non-vacuity. A substitution that matched nothing would leave one shell for every case
        # below, and each assertion would pass on whichever branch it happened to take — a fan-in
        # that ignores a dependency, in a test written to prove it does not.
        if expression not in script:
            raise AssertionError(f"{job} does not read {expression}")
        script = script.replace(expression, result)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def exemption_for(job: Job, upstream: str, result: str) -> FanInExemption | None:
    """The recorded exemption letting `job` accept `result` from `upstream`, if there is one."""
    wanted = (job.job_id, upstream, result)
    return next(
        (e for e in FAN_IN_EXEMPTIONS if (e.fan_in, e.upstream, e.accepted) == wanted), None
    )


def collapsed_readme() -> str:
    """README with runs of whitespace collapsed.

    Check names are prose here and the file wraps at 100 columns, so `Build devcontainer and smoke
    test` is split across two lines today. A literal search would report it missing and invite
    someone to "fix" the README by unwrapping a line.
    """
    return re.sub(r"\s+", " ", _README.read_text(encoding="utf-8"))


class VacuityTest(unittest.TestCase):
    """Every assertion below iterates something, and an empty something passes them all."""

    def test_workflows_were_found(self):
        self.assertTrue(_WORKFLOWS, f"no workflows under {_WORKFLOW_DIR}")

    def test_pull_request_jobs_were_found(self):
        self.assertTrue(_JOBS, "no job in any workflow runs on `pull_request`")

    def test_matrix_jobs_were_found(self):
        self.assertTrue([job for job in _JOBS if job.is_matrix], "no matrix job to check")

    def test_fan_ins_were_found(self):
        self.assertTrue(blocking_fan_ins(), "no fan-in sits on a required path")

    def test_the_manifest_is_populated(self):
        self.assertTrue(REQUIRED, "the required list is empty")
        self.assertTrue(NOT_REQUIRED, "the not-required list is empty")


class AccountingTest(unittest.TestCase):
    """Every job is classified, and every classification names a job that exists."""

    def test_every_job_is_accounted_for(self):
        for job in _JOBS:
            with self.subTest(job=str(job)):
                listed = [
                    label
                    for label, names in (("required", REQUIRED), ("not-required", NOT_REQUIRED))
                    if job.check in names
                ]
                if job.is_matrix:
                    self.assertEqual(
                        listed,
                        [],
                        f"`{job}` is a matrix job, so it belongs in neither list: its check name "
                        "carries the row that produced it, and a ruleset cannot hold that. Its "
                        "status comes from its fan-in instead.",
                    )
                    continue
                self.assertEqual(
                    len(listed),
                    1,
                    f"`{job.check}` ({job}) is in {listed or 'neither list'}. Put it in exactly "
                    "one. If it must block a merge, name it in REQUIRED *and* add it to the "
                    "ruleset — the ruleset is repo settings and this test cannot read it. "
                    "Otherwise put it in NOT_REQUIRED with a reason.",
                )

    def test_the_manifest_names_only_jobs_that_exist(self):
        """A renamed job leaves a manifest entry naming nothing — ruleset drift, one level down."""
        checks = {job.check for job in _JOBS if not job.is_matrix}
        for name in sorted(REQUIRED | set(NOT_REQUIRED)):
            with self.subTest(check=name):
                self.assertIn(
                    name,
                    checks,
                    f"the manifest names `{name}`, which no `pull_request` job reports. If the "
                    "job was renamed, the ruleset holds the old string too and is now naming a "
                    "check that never arrives — fix both.",
                )

    def test_every_not_required_entry_records_a_reason(self):
        for name, reason in sorted(NOT_REQUIRED.items()):
            with self.subTest(check=name):
                self.assertTrue(
                    reason.strip(),
                    f"`{name}` is on the not-required list with no reason, which is the omission "
                    "the list exists to turn into a decision",
                )


class ClosureTest(unittest.TestCase):
    """A scheduling edit must not move enforcement, in either direction."""

    def test_a_blocking_job_requires_only_blocking_jobs(self):
        for job in _JOBS:
            if not is_blocking(job):
                continue
            for needed_id in job.needs:
                needed = _BY_ID[(job.workflow, needed_id)]
                with self.subTest(job=str(job), needs=needed_id):
                    self.assertTrue(
                        is_blocking(needed),
                        f"`{needed.check}` does not block a merge, but `{job.check}` does and "
                        f"has `{needed_id}` in its `needs:` ({job.workflow}). So a failure there "
                        "now takes a merge gate down with it, and the recorded decision that it "
                        "is not binding was overturned by an edit that never mentioned it. "
                        "Either delete the edge or move the job to REQUIRED — and note the "
                        "direction: a non-blocking job *downstream* of a blocking one is fine, "
                        "which is the opposite arrangement to this one.",
                    )


class RequiredTriggerTest(unittest.TestCase):
    """A required check has to report on every PR, or it sits `Pending` and blocks every merge.

    The mirror of the skipped-reads-as-passed failure, and the more confusing one: a job filtered
    out of a run reports nothing at all, so branch protection waits forever for a check that was
    never going to arrive.
    """

    def filters(self, job: Job) -> dict:
        return triggers(_WORKFLOWS[job.workflow]).get(_PULL_REQUEST) or {}

    def required_jobs(self) -> list[Job]:
        return [job for job in _JOBS if job.check in REQUIRED]

    def test_no_required_check_is_filtered_by_path(self):
        for job in self.required_jobs():
            filters = self.filters(job)
            for key in ("paths", "paths-ignore"):
                with self.subTest(check=job.check, filter=key):
                    self.assertNotIn(
                        key,
                        filters,
                        f"{job.workflow} filters `pull_request` on `{key}:`, so it does not run "
                        f"on every PR — and `{job.check}` is required, so every PR that misses "
                        "the filter waits on a check that never reports. Classify inside the job "
                        "instead, the way devcontainer.yml does.",
                    )

    def test_no_required_check_is_filtered_off_the_protected_branch(self):
        for job in self.required_jobs():
            branches = self.filters(job).get("branches")
            if branches is None:
                continue
            with self.subTest(check=job.check):
                self.assertIn(
                    "main",
                    branches,
                    f"{job.workflow} does not run for PRs targeting `main`, which is the branch "
                    f"the ruleset protects and `{job.check}` is required on",
                )

    def test_no_required_check_misses_an_ordinary_pull_request_event(self):
        """`types:` narrower than the default silently drops the events a PR actually produces."""
        for job in self.required_jobs():
            kinds = self.filters(job).get("types")
            if kinds is None:
                continue
            for kind in ("opened", "synchronize"):
                with self.subTest(check=job.check, type=kind):
                    self.assertIn(
                        kind,
                        kinds,
                        f"{job.workflow} does not run on `{kind}`, so `{job.check}` never "
                        "reports for a PR that was only opened, or never re-reports after a "
                        "push — and a required check that does not report blocks the merge",
                    )


class MatrixTest(unittest.TestCase):
    """A matrix job is requirable only through a fan-in, and only while it has rows to run."""

    def matrix_jobs(self) -> list[Job]:
        return [job for job in _JOBS if job.is_matrix]

    def test_every_matrix_job_has_exactly_one_fan_in(self):
        for job in self.matrix_jobs():
            found = [fan_in.job_id for fan_in in fan_ins_of(job)]
            with self.subTest(job=str(job)):
                self.assertEqual(
                    len(found),
                    1,
                    f"`{job}` has {len(found)} non-matrix jobs depending on it ({found or 'none'})."
                    " With none it is a check no ruleset can name, and nothing about it looks "
                    "wrong — it runs, it reports, and it gates nothing, which is the state "
                    "`golangci-lint` was in. With several there is no single name to require on "
                    "its behalf.",
                )

    def test_every_matrix_declares_at_least_one_row(self):
        for job in self.matrix_jobs():
            with self.subTest(job=str(job)):
                try:
                    rows = matrix_rows(job)
                except Uncountable as exc:
                    self.fail(
                        f"`{job}`: {exc}. A matrix this file cannot count is one it cannot vouch "
                        "for, and passing it would be a gate standing down for exactly the "
                        "commit that needed it. See #271."
                    )
                self.assertGreaterEqual(
                    rows,
                    1,
                    f"`{job}` declares no rows, so it reports `skipped` — which its fan-in may "
                    "accept, leaving a required check green over a matrix that ran nothing",
                )


class FanInStrictnessTest(unittest.TestCase):
    """What makes requiring a fan-in mean anything about the rows underneath it.

    Without this the manifest proves a check is reachable from a required name, not that reaching
    it has any consequence — and "reachable but inconsequential" is the bug in #310.
    """

    def test_a_fan_in_runs_even_when_its_matrix_failed(self):
        for job in blocking_fan_ins():
            with self.subTest(job=str(job)):
                self.assertIn(
                    job.body.get("if"),
                    _ALWAYS,
                    f"without `always()` a failed matrix skips `{job}`, and branch protection "
                    "counts a skipped required check as passed. `always()` rather than "
                    "`!cancelled()` here on purpose: seeing a `cancelled` result and refusing it "
                    "is what keeps a cancelled run from reading as a pass.",
                )

    def test_a_fan_in_passes_when_every_row_succeeded(self):
        for job in blocking_fan_ins():
            with self.subTest(job=str(job)):
                done = run_fan_in(job, dict.fromkeys(job.needs, "success"))
                self.assertEqual(done.returncode, 0, f"{done.stdout}{done.stderr}".strip())

    def test_a_fan_in_refuses_every_result_it_has_no_exemption_for(self):
        for job in blocking_fan_ins():
            for upstream in job.needs:
                for result in _NOT_SUCCESS:
                    results = dict.fromkeys(job.needs, "success")
                    results[upstream] = result
                    exemption = exemption_for(job, upstream, result)
                    done = run_fan_in(job, results)
                    with self.subTest(job=str(job), upstream=upstream, result=result):
                        if exemption is None:
                            self.assertNotEqual(
                                done.returncode,
                                0,
                                f"`{job}` accepts `{result}` from `{upstream}`, so requiring it "
                                "does not require that job. Either make it strict, or record an "
                                "exemption in FAN_IN_EXEMPTIONS saying why this one is benign.",
                            )
                        else:
                            self.assertEqual(
                                done.returncode,
                                0,
                                f"FAN_IN_EXEMPTIONS says `{job}` accepts `{result}` from "
                                f"`{upstream}`, but it refuses it. The exemption claims more than "
                                f"the shell does — delete it. Recorded reason: {exemption.reason}",
                            )


class ExemptionTest(unittest.TestCase):
    """An exemption for an edge that no longer exists is a decision nobody is making any more."""

    def test_every_exemption_names_an_edge_that_exists(self):
        edges = {(job.job_id, needed) for job in _JOBS for needed in job.needs}
        for exemption in FAN_IN_EXEMPTIONS:
            with self.subTest(fan_in=exemption.fan_in, upstream=exemption.upstream):
                self.assertIn(
                    (exemption.fan_in, exemption.upstream),
                    edges,
                    f"`{exemption.fan_in}` no longer has `{exemption.upstream}` in its `needs:`, "
                    "so this exemption weakens nothing and explains nothing — remove it",
                )

    def test_every_exemption_records_a_reason(self):
        for exemption in FAN_IN_EXEMPTIONS:
            with self.subTest(fan_in=exemption.fan_in, upstream=exemption.upstream):
                self.assertTrue(exemption.reason.strip(), "an exemption with no reason is a hole")


class DocumentedTest(unittest.TestCase):
    """README is where a reader learns the exceptions; the manifest is where they are decided.

    Deliberately narrow. This asserts the exceptions and the fan-in names survive in the prose, not
    that every sentence in README agrees with the manifest — README's classification is three
    sentences with the exceptions embedded in them, and parsing that would either restate it or
    accept anything. What it catches is the drift that actually happens: an entry added to
    NOT_REQUIRED and never written up, so the only record of it is a file nobody reads for prose.
    """

    def test_readme_names_every_not_required_check(self):
        readme = collapsed_readme()
        for name in sorted(NOT_REQUIRED):
            with self.subTest(check=name):
                self.assertIn(
                    name,
                    readme,
                    f"README does not name `{name}`, which does not block a merge. The list of "
                    "things that gate nothing is exactly what a reader cannot infer from the "
                    "workflows, so it has to survive in the prose.",
                )

    def test_readme_names_every_fan_in(self):
        readme = collapsed_readme()
        for job in blocking_fan_ins():
            with self.subTest(check=job.check):
                self.assertIn(
                    job.check,
                    readme,
                    f"README does not name `{job.check}`, the check the ruleset requires on "
                    f"`{job.job_id}`'s matrix's behalf",
                )


if __name__ == "__main__":
    unittest.main()
