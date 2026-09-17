"""What the `main` ruleset requires, written down where something can read it.

Enforcement is repo settings. The ruleset holds a list of check-name strings and nothing in this
tree can read it, so a reader of ci.yml cannot tell whether `ruff` failing blocks a merge, and an
author adding a job cannot tell whether they added a check or a decoration. The default outcome for
a new job is "enforces nothing", and it is silent — #310, #311 and #312 were three instances of
that one defect. This module is the claim; //meta/scripts:test_ci_enforcement_manifest is what
holds the workflows to it.

**Not self-verifying: a green test is not a verified ruleset.** This is a tree-local claim about
settings nothing here can read, so editing the ruleset in the GitHub UI and not this file leaves
the test asserting a fiction — the same failure class it exists to prevent, one level up. That gap
is accepted and recorded rather than papered over; closing it against the live API is #314.

Two axes cross here, and the lists divide on only one of them:

  - A **check** verifies an invariant and has no side effects. An **automated task** acts on the
    repo's behalf and does. Three of the four NOT_REQUIRED entries are tasks, which is why they
    gate nothing without being decorations. Nothing branches on the distinction, so it is not a
    field — each reason string says which it is.
  - **Blocking** or not, relative to the gate of the context a job runs in. In CI that means named
    in the ruleset. This is the axis REQUIRED and NOT_REQUIRED divide on.

Matrix jobs are in neither list, deliberately. A row-carrying check name — `golangci-lint
(tools/foo)` — is one a ruleset cannot hold, so "named in REQUIRED" is not a category a matrix job
can fail to be in; it is one it cannot express. A matrix job's status is *derived* from its fan-in
instead, which is why the test insists every matrix job has exactly one.
"""

from __future__ import annotations

from typing import NamedTuple

# The ruleset these lists mirror, for #314 and for anyone reaching for the API by hand:
# `gh api repos/Syndic/unnatural_designs/rulesets/14538709`. Writable nowhere in this tree.
RULESET_ID = 14538709

# Check names, not job ids: the ruleset holds strings, and a job's check name is its `name:` with
# `${{ }}` interpolated. Keying by the string is what lets #314 diff this against the live list
# without first having to agree with it about what a job is called.
REQUIRED = frozenset(
    {
        # ci.yml
        "ADR number uniqueness check",
        "Build and test (all targets)",
        "Gazelle BUILD file check",
        "MODULE.bazel.lock freshness",
        "Module completeness check",
        "No-cgo policy check",
        "Python version consistency check",
        "Secrets check",
        "go.work consistency/completeness check",
        "golangci-lint (all modules)",
        "ruff",
        "shellcheck",
        "ty",
        # commit-file-via-app-selftest.yml
        "Action self-test",
        # devcontainer.yml
        "Base image (all platforms)",
        "Build devcontainer and smoke test",
        # Required as of #313. Both jobs above carry `needs: changes` and both are required, so a
        # classification nobody requires put two merge gates downstream of an unguarded job. The
        # explicit `needs.changes.result` reads #316 added stay regardless: they are what makes a
        # green check mean something, where this entry only makes the merge block.
        "Detect devcontainer changes",
        # security.yml
        "CodeQL Analysis (all languages)",
        "Semgrep",
        "Trivy",
        "govulncheck (all modules)",
        "pip-audit",
    }
)

# Deliberately not binding, one reason each. This is the load-bearing list: it is where "this does
# not block a merge" becomes a decision someone wrote down instead of an omission nobody made on
# purpose. Membership is decided statically, by workflow trigger, so a job that never runs on an
# ordinary PR still needs an entry — and saying why is the only place that fact is recorded.
NOT_REQUIRED = {
    "Coverage": (
        "Advisory check. Its failure is a judgement call rather than a defect, and Codecov's own "
        "project/patch statuses are threshold-based."
    ),
    "Publish the shared base image": (
        "Automated task: pushes the base image to GHCR, where Syndic/.dotfiles expects to find "
        "it. Gated to `github.event_name == 'push'` on main, so it never reports on a PR at all."
    ),
    "Re-derive lock files": (
        "Automated task: regenerates the derived files Mend-hosted Renovate cannot and commits "
        "them back to the PR. Gated to renovate[bot], and its workflow keeps a trigger-level "
        "`paths:` — which stays legal only while nothing requires it."
    ),
    "Request a Renovate run": (
        "Automated task: ticks the Dependency Dashboard checkbox so Renovate rebases its other "
        "PRs after automerging one of its own. Runs on `types: [closed]`, so it never reports on "
        "an open PR."
    ),
}


class FanInExemption(NamedTuple):
    """One (fan-in, upstream, result) triple the strictness rule deliberately accepts.

    Job ids rather than check names, because this describes a `needs:` edge inside one workflow
    and that is what an edge is spelled with.
    """

    fan_in: str
    upstream: str
    accepted: str
    reason: str


# Every fan-in on a required path must fail on any result that is not `success` — including
# `skipped`, which is what GitHub reports for a job removed from the run and what branch protection
# counts as a pass. One exemption exists. It lives here as data rather than inside whichever test
# was written first, so the generalised strictness check can see it and so a second one cannot be
# added without appearing in this file.
FAN_IN_EXEMPTIONS = (
    FanInExemption(
        fan_in="base-image-all",
        upstream="base-image",
        accepted="skipped",
        reason=(
            "`base-image` is path-gated on `needs.changes.outputs.base`, so a skipped matrix is "
            "the ordinary no-op path rather than rows left unrun. The fan-in stays strict about "
            "`changes` itself, since a gate that did not run is not a gate that said no — which "
            "is why the exemption names one upstream job and not the whole fan-in."
        ),
    ),
)
