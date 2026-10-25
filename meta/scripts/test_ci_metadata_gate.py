"""Holds ci.yml's content checks behind its metadata checks, and behind nothing else.

Scheduling, not enforcement: the rule, why it is drawn there and what it costs are
.claude/CLAUDE.md's, under "Enforcement lives in the ruleset, not in `needs`". This file holds the
shape:

  - every job is classified metadata or content, in both directions;
  - every content job waits, directly or through another job, for every metadata job;
  - no metadata job waits on anything, so one cannot skip and hide another's answer;
  - a content job waits on another content job only where that edge is recorded here.

Classification is a recorded decision rather than a derived one. Whether a job parses source is not
reliably readable from workflow text, so each entry says what the job compares or reads, the way
the enforcement manifest's lists carry a reason each.
"""

import unittest
from pathlib import Path

from meta.scripts._workflows import job_needs, step_containers

# Left unresolved because the workflow is a cross-package data dep, so it lives in the runfiles tree
# beside this file rather than at the source path a resolved symlink would lead back to.
_WORKFLOW = Path(__file__).parent.parent.parent / ".github/workflows/ci.yml"
_JOBS = {container.label: container.scope for container in step_containers(_WORKFLOW)}

# The tree's declarations — manifests, locks, registration, layout — checked against each other and
# against what generates them. None parses source. Each value says what it compares.
_METADATA = {
    "bazel-lock-check": "MODULE.bazel and its lock against `bazel mod tidy`",
    "modules-check": "module registration, and requirements_lock.txt against uv.lock",
    "go-work-check": "every go.mod against go.work's `use` entries",
    "adr-numbers-check": "ADR file names",
    "python-version-check": "every language-level declaration against //:.python-version",
    "secrets-check": "the names of tracked files under secrets/",
}

# Reads code, or builds and tests it. Each value says what it reads.
_CONTENT = {
    "gazelle-check": (
        "every Go and Python import, to derive the BUILD files. BUILD files are build "
        "configuration, but per-package and generated from source; and gazelle's answer moves "
        "with the module metadata the gate checks, so it consumes the gate rather than joining it"
    ),
    "no-cgo-check": 'Go source for `import "C"`, and `go list -deps`',
    "golangci-lint": "Go source, per module",
    "golangci-lint-all": "its matrix's results",
    "ruff": "Python source",
    "ty": "Python source",
    "shellcheck": "shell scripts",
    "actionlint": "the programs in workflow files: expressions, and shell in `run:` blocks",
    "build-and-test-per-target": "everything, per platform",
    "build-and-test-all": "its matrix's results",
    "coverage": "everything, once more under coverage instrumentation",
    "coverage-upload": "the lcov artifact `coverage` produced",
}

# The content-to-content edges allowed, each for a reason that is not ordering for its own sake.
_CONTENT_EDGES = {
    ("golangci-lint-all", "golangci-lint"): "a fan-in over its own matrix",
    ("build-and-test-all", "build-and-test-per-target"): "a fan-in over its own matrix",
    ("coverage-upload", "coverage"): "uploads the artifact `coverage` writes",
}


def upstream_of(job_id: str) -> set[str]:
    """Every job reachable from `job_id` through `needs:`, however many hops away.

    Transitive on purpose: a fan-in reaches the gate through the matrix it aggregates, and that
    gates it just as well as an edge of its own would.
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

    def test_both_classes_are_populated(self):
        self.assertTrue(_METADATA, "no metadata check, so the gate gates nothing")
        self.assertTrue(_CONTENT, "no content check, so nothing waits on the gate")


class AccountingTest(unittest.TestCase):
    """Every job arrives classified, and every classification names a job."""

    def test_every_job_is_classified_exactly_once(self):
        for job_id in sorted(_JOBS):
            listed = [
                label
                for label, entries in (("metadata", _METADATA), ("content", _CONTENT))
                if job_id in entries
            ]
            with self.subTest(job=job_id):
                self.assertEqual(
                    len(listed),
                    1,
                    f"`{job_id}` is {listed or 'in neither list'}. Put it in exactly one: metadata "
                    "if it checks the tree's declarations and never parses source, content if it "
                    "reads code or builds it. Say what it compares or reads.",
                )

    def test_every_classification_names_a_job(self):
        for job_id in sorted(set(_METADATA) | set(_CONTENT)):
            with self.subTest(job=job_id):
                self.assertIn(
                    job_id,
                    _JOBS,
                    f"`{job_id}` is classified here but is not a job in {_WORKFLOW.name}. If it "
                    "was renamed, this file is now silent about the job that replaced it.",
                )

    def test_every_entry_says_why(self):
        for label, entries in (("metadata", _METADATA), ("content", _CONTENT)):
            for job_id, reason in sorted(entries.items()):
                with self.subTest(job=job_id, list=label):
                    self.assertTrue(
                        reason.strip(), f"`{job_id}` is classified {label} with no reason"
                    )


class GateTest(unittest.TestCase):
    """The shape the gate is."""

    def test_every_content_job_waits_for_every_metadata_job(self):
        for job_id in sorted(_CONTENT):
            upstream = upstream_of(job_id)
            for gate in sorted(_METADATA):
                with self.subTest(job=job_id, gate=gate):
                    self.assertIn(
                        gate,
                        upstream,
                        f"`{job_id}` reads code without waiting for `{gate}`, which checks "
                        f"{_METADATA[gate]} — so it can do real work on a tree whose declarations "
                        "are already known to be broken. Give it `needs: *metadata-gate`.",
                    )

    def test_no_metadata_job_waits_on_anything(self):
        """A metadata check behind another would skip when it fails, and hide its own answer."""
        for job_id in sorted(_METADATA):
            with self.subTest(job=job_id):
                self.assertEqual(
                    job_needs(_JOBS[job_id]),
                    (),
                    f"`{job_id}` is a metadata check with a `needs:`. If what it needs fails, it "
                    "reports `skipped` instead of its own verdict, and the author fixes one "
                    "declaration only to learn about the next on the following push.",
                )


class ContentEdgeTest(unittest.TestCase):
    """Content checks are peers: one waits on another only for a recorded reason."""

    def test_every_content_to_content_edge_is_recorded(self):
        for job_id in sorted(_CONTENT):
            for needed in job_needs(_JOBS[job_id]):
                if needed not in _CONTENT:
                    continue
                with self.subTest(job=job_id, needs=needed):
                    self.assertIn(
                        (job_id, needed),
                        _CONTENT_EDGES,
                        f"`{job_id}` waits on `{needed}`, another content check, so a failure in "
                        f"`{needed}` now hides `{job_id}`'s result. Content checks report "
                        "independently; record the edge with its reason only if it is a fan-in "
                        "over its own matrix or consumes an artifact.",
                    )

    def test_every_recorded_edge_exists(self):
        """A recorded edge the workflow no longer has explains nothing."""
        for (job_id, needed), reason in sorted(_CONTENT_EDGES.items()):
            with self.subTest(job=job_id, needs=needed):
                self.assertTrue(reason.strip(), "a recorded edge with no reason is a hole")
                self.assertIn(
                    needed,
                    job_needs(_JOBS.get(job_id, {})),
                    f"`{job_id}` no longer waits on `{needed}`; remove the recorded edge",
                )


if __name__ == "__main__":
    unittest.main()
