"""Holds every `pull_request` job to the enforcement manifest, and the manifest to the workflows.

`ci_enforcement_manifest.py` is a demand about repo settings. This file is what makes the demand
do work: it fails a job nobody classified, an edge that moved enforcement without saying so, a
required check that cannot report, a matrix nothing can require, and a fan-in that accepts a result
it should refuse.

The properties, and why each one is not implied by the others:

  - **Accounting.** Every job is required, not-required, or a matrix job. Without it a new job
    enforces nothing by default and silently, which is the whole defect.
  - **The closure rule.** A blocking job requires only blocking jobs. Accounting says each job was
    classified; this says a `needs:` edit did not quietly overrule the classification, in either
    direction — a required check unhooked to parallelise it, or a non-required job pulled into a
    required job's `needs` so its failure now takes a merge gate down.
  - **Reach.** A required check runs, and reports honestly, on every PR — which its workflow's
    trigger, its own `if:` and `continue-on-error` can each undo, in opposite directions. See
    `RequiredReachTest`.
  - **Matrix shape.** Every matrix job has exactly one fan-in and at least one row. The fan-in is
    the only name a ruleset can require on a matrix's behalf; the row count is what stops an empty
    matrix from reporting `skipped` into a fan-in that accepts `skipped`.
  - **Fan-in strictness.** Requiring a fan-in means something only if it refuses what its rows
    report. Without this the manifest proves a check is *reachable* from a required name, not that
    reaching it has any consequence — and "reachable but inconsequential" was #310.

What none of it can do is read the rules, so none of it can tell you the repository meets the
demand. `check_repository_constraints.py` is what asks GitHub; this file assumes the answer and
holds the workflows to it.

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
from meta.scripts._workflows import job_condition, job_needs
from meta.scripts.ci_enforcement_manifest import (
    FAN_IN_EXEMPTIONS,
    NOT_REQUIRED,
    PROTECTED_BRANCH,
    FanInExemption,
    required_check_names,
)

# Projected out of the manifest's RULES rather than kept as a list of its own. A second copy of
# these names could disagree with the one the repository is actually held to, and the whole point
# of the manifest is that there is one demand to check.
_REQUIRED = required_check_names()

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

# The two conditions that run whatever happened upstream. A required job may carry one of these
# and nothing else: GitHub reports a job skipped by a false `if:` as `skipped`, and branch
# protection counts `skipped` as a pass, so any falsifiable condition on a required job makes it a
# decoration on every run the condition is false. `always()` is what the fan-ins carry;
# `!cancelled()` is what `build-and-smoke-test` carries.
_ALWAYS_RUN = frozenset({"always()", "!cancelled()"})

# The branch the rules protect, and so the one a required check has to report on. Read from the
# manifest, which is also what the comparison against GitHub asks about.
_PROTECTED_BRANCH = PROTECTED_BRANCH

# GitHub's filter-pattern characters. A branch list using any of them needs glob semantics to
# resolve, which this file refuses to guess at rather than approximate.
_GLOB_RE = re.compile(r"[*?\[\]!+]")

# Matrix sections rather than axes: neither is a dimension of the cross product.
_INCLUDE = "include"
_EXCLUDE = "exclude"

# Required checks README documents in the paragraphs above its job tables rather than in a row,
# because those paragraphs are where their workflow's path gating is explained and a table cell
# cannot carry it. README names all four in one sentence directly under the tables.
#
# Data rather than a loosened assertion, for the reason FAN_IN_EXEMPTIONS is: a fifth cannot be
# added without appearing here, where it is visible and needs a reason. Adding a name to quiet a
# red is the failure mode; the tests below make a stale or untrue entry its own failure.
_DOCUMENTED_IN_PROSE = frozenset(
    {
        "Action self-test",
        "Base image (all platforms)",
        "Build devcontainer and smoke test",
        "Detect devcontainer changes",
    }
)


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


def _pull_request_jobs() -> list[Job]:
    """Every job in every workflow that runs on `pull_request`.

    Membership is decided by the trigger and never by evaluating an `if:`. Three jobs here never
    run on an ordinary PR — `publish`, `re-derive`, `request-run` — and are in scope anyway.
    Approximating GitHub's expression semantics would drop jobs from the domain, and a job dropped
    from the domain is one this file passes without checking. The cost is three NOT_REQUIRED
    entries; what they buy is that "never runs on a PR" is written down somewhere at all.

    That argument is about *membership* only, and does not reach a required job's own gate:
    `RequiredReachTest` refuses an `if:` on a required job rather than trying to evaluate one,
    which needs no expression semantics and is the same fail-closed answer from the other side.
    """
    found = []
    for name, workflow in sorted(_WORKFLOWS.items()):
        if _PULL_REQUEST not in triggers(workflow):
            continue
        for job_id, body in (workflow.get("jobs") or {}).items():
            found.append(Job(name, job_id, body.get("name") or job_id, job_needs(body), body))
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
        return job.check in _REQUIRED
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

    Executed rather than pattern-matched: asserting on the spelling would fail an idiom that behaves
    identically and pass one that does not. The tree holds two idioms — `if` fan-ins and
    `base-image-all`'s `case` — and neither is privileged here.
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


def branch_filter_problem(filters: dict) -> str | None:
    """Why a `pull_request:` filter might keep a required check off the protected branch.

    `branches-ignore:` is refused outright rather than read: it can only subtract, and deciding
    whether it subtracts the protected branch needs GitHub's own glob semantics. A pattern in
    `branches:` is refused for the same reason, and refused rather than waved through because the
    direction that matters — a filter that quietly stops admitting `main` — is the one a guess
    would get wrong. Every workflow here lists the branch literally.
    """
    if "branches-ignore" in filters:
        return (
            "`branches-ignore:` can only remove branches from the set, and whether it removes "
            f"`{_PROTECTED_BRANCH}` needs GitHub's glob semantics rather than a literal read"
        )
    branches = filters.get("branches")
    if branches is None:
        return None
    if not isinstance(branches, list):
        return "`branches:` is not a list, so the branches it admits cannot be read here"
    if _PROTECTED_BRANCH in branches:
        return None
    if any(isinstance(b, str) and _GLOB_RE.search(b) for b in branches):
        return (
            f"`branches:` uses a pattern, and whether it admits `{_PROTECTED_BRANCH}` needs "
            "GitHub's glob semantics rather than a literal match — teach this function"
        )
    return f"`branches:` does not list `{_PROTECTED_BRANCH}`"


def exemption_for(job: Job, upstream: str, result: str) -> FanInExemption | None:
    """The recorded exemption letting `job` accept `result` from `upstream`, if there is one."""
    wanted = (job.job_id, upstream, result)
    return next(
        (e for e in FAN_IN_EXEMPTIONS if (e.fan_in, e.upstream, e.accepted) == wanted), None
    )


def exempted_fan_in(exemption: FanInExemption) -> Job:
    """The one blocking fan-in an exemption names. Job ids are per workflow, so two is a failure."""
    found = [job for job in blocking_fan_ins() if job.job_id == exemption.fan_in]
    if len(found) != 1:
        raise AssertionError(
            f"FAN_IN_EXEMPTIONS names `{exemption.fan_in}`, which matches {len(found)} blocking "
            "fan-ins; it has to name exactly one"
        )
    return found[0]


def collapsed_readme() -> str:
    """README with runs of whitespace collapsed.

    Check names are prose here and the file wraps at 100 columns, so `Build devcontainer and smoke
    test` is split across two lines today. A literal search would report it missing and invite
    someone to "fix" the README by unwrapping a line.
    """
    return re.sub(r"\s+", " ", _README.read_text(encoding="utf-8"))


def readme_job_table_checks() -> set[str]:
    """The check names in the first column of README's job tables.

    Three narrowings, each closing a way the assertion could pass without meaning anything.

    *The job tables only*, identified by their own `| Job |` header rather than by section
    position. README has nine tables and two of them enumerate jobs; a name in the pre-commit hook
    table says nothing about whether a merge gate is documented.

    *The first column only*, because that is where a job is named — a check mentioned in a
    neighbouring cell's prose is not a row.

    *Whole cells, not substrings*. `ty` is two characters and occurs inside ordinary words, so a
    substring search over the tables would pass for it no matter what they contained.
    """
    checks: set[str] = set()
    in_job_table = False
    for line in _README.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            in_job_table = False
            continue
        row = [
            re.sub(r"\s+", " ", cell).strip().strip("`").strip()
            for cell in stripped.strip("|").split("|")
        ]
        if not in_job_table:
            # The header names the column; the separator row below it is skipped by the same test.
            in_job_table = bool(row) and row[0] == "Job"
            continue
        if row and not set("".join(row)) <= set("-: "):
            checks.add(row[0])
    return checks


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
        self.assertTrue(_REQUIRED, "the rules require no checks")
        self.assertTrue(NOT_REQUIRED, "the not-required list is empty")


class AccountingTest(unittest.TestCase):
    """Every job is classified, and every classification names a job that exists."""

    def test_every_job_is_accounted_for(self):
        for job in _JOBS:
            with self.subTest(job=str(job)):
                listed = [
                    label
                    for label, names in (("required", _REQUIRED), ("not-required", NOT_REQUIRED))
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
                    "one. If it must block a merge, add it to the repository's rules *and* to "
                    "RULES, which is the demand on them. Otherwise put it in NOT_REQUIRED with "
                    "a reason.",
                )

    def test_the_manifest_names_only_jobs_that_exist(self):
        """A renamed job leaves a manifest entry naming nothing — ruleset drift, one level down."""
        checks = {job.check for job in _JOBS if not job.is_matrix}
        for name in sorted(_REQUIRED | set(NOT_REQUIRED)):
            with self.subTest(check=name):
                self.assertIn(
                    name,
                    checks,
                    f"the manifest names `{name}`, which no `pull_request` job reports. If the "
                    "job was renamed, the repository's rules hold the old string too and are now "
                    "naming a check that never arrives — fix both.",
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
                        "Either delete the edge or make the job blocking — and note the "
                        "direction: a non-blocking job *downstream* of a blocking one is fine, "
                        "which is the opposite arrangement to this one.",
                    )


class RequiredReachTest(unittest.TestCase):
    """A required check has to run, and report honestly, on every PR.

    Two failures with opposite shapes, which is why the trigger is not the whole question. A
    workflow filtered out of a run reports *nothing*, so branch protection waits forever on a
    check that was never going to arrive. A job that runs but is gated off by its own `if:`
    reports `skipped`, which branch protection counts as a **pass** — so the check is a decoration
    on every run its condition is false. The first blocks every merge and is impossible to miss;
    the second blocks none and is invisible, which makes it the one worth testing for.
    """

    def filters(self, job: Job) -> dict:
        return triggers(_WORKFLOWS[job.workflow]).get(_PULL_REQUEST) or {}

    def required_jobs(self) -> list[Job]:
        return [job for job in _JOBS if job.check in _REQUIRED]

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
            problem = branch_filter_problem(self.filters(job))
            with self.subTest(check=job.check):
                self.assertIsNone(
                    problem,
                    f"{job.workflow} may not run for PRs targeting `{_PROTECTED_BRANCH}`, which "
                    f"is the branch the ruleset protects and `{job.check}` is required on: "
                    f"{problem}",
                )

    def test_no_required_check_is_gated_by_its_own_condition(self):
        """The trigger decides whether the workflow runs; this decides whether the job does."""
        for job in self.required_jobs():
            gate = job_condition(job.body)
            if gate is None:
                continue
            with self.subTest(check=job.check):
                self.assertIn(
                    gate,
                    _ALWAYS_RUN,
                    f"`{job.check}` is required but carries `if: {gate}`, so on every run that "
                    "condition is false the job reports `skipped` — which branch protection "
                    "counts as a pass. A required check with a falsifiable gate gates nothing. "
                    f"Only {sorted(_ALWAYS_RUN)} are allowed here, since they always run; a job "
                    "that genuinely should not always run belongs in NOT_REQUIRED, and work it "
                    "should sometimes skip belongs behind a step-level condition instead.",
                )

    def test_no_required_check_tolerates_its_own_failure(self):
        """The same place by the other door: `continue-on-error` is a red job reporting green."""
        for job in self.required_jobs():
            declared = job.body.get("continue-on-error")
            if declared is None or declared is False:
                continue
            with self.subTest(check=job.check):
                self.fail(
                    f"`{job.check}` is required but carries `continue-on-error: {declared}`, so "
                    "it reports success however it exits, and requiring it asks nothing of it"
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
    """What makes requiring a fan-in mean anything about the rows underneath it."""

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

    def test_an_exemption_never_hides_another_upstreams_failure(self):
        """Failures arrive together: a gate that fails skips the matrix it gates.

        The test above changes one upstream at a time, holding the rest at `success`, so it never
        sees the exempted result arrive *with* a failure. A fan-in that stops at the exempted
        result — `skipped` from `base-image`, say — before asking about the others passes it, and
        passes exactly the run where `changes` failed and left `base-image` skipped behind it.
        """
        for exemption in FAN_IN_EXEMPTIONS:
            job = exempted_fan_in(exemption)
            for upstream in job.needs:
                if upstream == exemption.upstream:
                    continue
                for result in _NOT_SUCCESS:
                    if exemption_for(job, upstream, result) is not None:
                        continue
                    results = dict.fromkeys(job.needs, "success")
                    results[exemption.upstream] = exemption.accepted
                    results[upstream] = result
                    done = run_fan_in(job, results)
                    with self.subTest(job=str(job), upstream=upstream, result=result):
                        self.assertNotEqual(
                            done.returncode,
                            0,
                            f"`{job}` accepts `{result}` from `{upstream}` whenever "
                            f"`{exemption.upstream}` reports `{exemption.accepted}`, so the "
                            "exemption waives more than the one edge it names",
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
    """README is where a reader learns what gates a merge; the manifest is where it is decided.

    Deliberately narrow: this asserts the names survive in the prose, not that every sentence in
    README agrees with the manifest. README's classification is three sentences with the
    exceptions embedded in them, and parsing that would either restate it or accept anything.

    What it catches is the drift that actually happens — a check added to one side and never
    written up on the other, leaving the only record in a file nobody reads for prose. That runs
    in both directions and both are covered: an entry added to NOT_REQUIRED without a mention, and
    a required check that README's tables never list while the sentence under them still claims
    every job in them is required. The second is how
    `Repository constraint enforcement manifest consistency check` shipped in #328 — the 23rd
    required context, named nowhere a reader enumerating the gates would find it.
    """

    def test_every_required_check_is_in_a_table_or_recorded_as_an_exception(self):
        listed = readme_job_table_checks()
        for name in sorted(_REQUIRED):
            with self.subTest(check=name):
                self.assertTrue(
                    name in listed or name in _DOCUMENTED_IN_PROSE,
                    f"README's job tables do not list `{name}`, which blocks a merge, and it is "
                    "not in _DOCUMENTED_IN_PROSE. The sentence under those tables says every job "
                    "in them is required, so a reader enumerating the gates comes up short. Add "
                    "the row, or add the name here with the reason it lives in prose instead.",
                )

    def test_the_job_tables_were_found(self):
        """A parse that found nothing would make the assertion above unfalsifiable-looking."""
        self.assertTrue(readme_job_table_checks(), "README's job tables did not parse")

    def test_no_prose_exception_has_gone_stale(self):
        """An exception for a check that is no longer required would hide the next omission."""
        for name in sorted(_DOCUMENTED_IN_PROSE):
            with self.subTest(check=name):
                self.assertIn(
                    name,
                    _REQUIRED,
                    f"`{name}` is recorded as documented in prose but no longer blocks a merge",
                )

    def test_every_prose_exception_is_actually_in_the_readme(self):
        """The exception says "documented elsewhere", which is only true if it is."""
        readme = collapsed_readme()
        for name in sorted(_DOCUMENTED_IN_PROSE):
            with self.subTest(check=name):
                self.assertIn(
                    name,
                    readme,
                    f"`{name}` is exempt from the table rule on the grounds that README's prose "
                    "names it, and README's prose does not name it",
                )

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
