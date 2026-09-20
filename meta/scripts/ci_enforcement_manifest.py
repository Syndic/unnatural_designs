"""What constrains `main`, written down where something can read it.

Enforcement is repo settings. The rules hold a list of check-name strings and a merge policy, and
nothing in this tree can read them, so a reader of ci.yml cannot tell whether `ruff` failing blocks
a merge, and an author adding a job cannot tell whether they added a check or a decoration. The
default outcome for a new job is "enforces nothing", and it is silent — #310, #311 and #312 were
three instances of that one defect. This module is the demand.
`//meta/scripts:test_ci_enforcement_manifest` holds the workflows to it, and
`check_repository_constraints.py` holds *it* to what GitHub reports.

**This is a demand, not a mirror.** A mirror that updated itself would be carried along by the
rules it describes: a required check dropped in the UI would be adopted on the next run and the
gate would vanish behind a green tick. Changing the demand is therefore a change like any other,
and has to pass through review to become a decision rather than a drift. Drafting that change is
unconstrained — see #329. ADR 0003, `the-enforcement-manifest-is-a-demand-not-a-mirror`, carries
that decision, why the rules are read as the effective set on the branch rather than as a ruleset
named by id, and why the comparison is equality over every rule rather than an allowlist of the
ones that seem to matter.

Two exclusions are known and named rather than closed, both for want of `Administration: read`:
`bypass_actors`, which the branch endpoint does not return without write access, so these rules can
match exactly while an actor may bypass them; and the `Basic Tag Rules` ruleset, which a branch
endpoint cannot reach at all and which gates no merge.

Two axes cross here, and NOT_REQUIRED divides on only one of them:

  - A **check** verifies an invariant and has no side effects. An **automated task** acts on the
    repo's behalf and does. Three of the four NOT_REQUIRED entries are tasks, which is why they
    gate nothing without being decorations. Nothing branches on the distinction, so it is not a
    field — each reason string says which it is.
  - **Blocking** or not, relative to the gate of the context a job runs in. In CI that means named
    in the rules. RULES answers this for a job; NOT_REQUIRED records the jobs for which the answer
    is deliberately no.

Matrix jobs are in neither place, deliberately. A row-carrying check name — `golangci-lint
(tools/foo)` — is one a ruleset cannot hold, so "named in the rules" is not a category a matrix job
can fail to be in; it is one it cannot express. A matrix job's status is *derived* from its fan-in
instead, which is why the test insists every matrix job has exactly one.
"""

from __future__ import annotations

from typing import Final, NamedTuple

# The branch these rules govern, and so the one a required check has to report on. Both consumers
# read it here: the comparison asks GitHub for this branch, and the workflow test refuses a
# required check that a `branches:` filter could keep off it.
PROTECTED_BRANCH: Final = "main"

# Dropped from every rule before comparison. `ruleset_id` says which ruleset a rule arrived from,
# which is not a constraint on anything: it changes if the ruleset is deleted and recreated with
# identical rules, and asserting it would reinstate the id coupling ADR 0003 removed.
# `ruleset_source` is kept — a repo rename is worth a red — and so is each context's
# `integration_id`, which asserts that these checks come from Actions rather than from some other
# app installed later and posting statuses under the same names.
IGNORED_RULE_FIELDS: Final = frozenset({"ruleset_id"})

# Every rule effectively applying to PROTECTED_BRANCH — the union of whatever rulesets reach it,
# not one ruleset's contents. Stored sorted, and compared without regard to order, because the
# API's order is storage order and nothing here depends on it.
#
# Two parameters are load-bearing beyond the check-name list, and are the reason this mirrors every
# rule rather than just `required_status_checks`:
# `required_status_checks.strict_required_status_checks_policy` is why a `BEHIND` branch cannot
# merge, which is the premise `renovate-run-after-automerge.yml` exists on; and `code_scanning`
# gates on what CodeQL and Trivy *found* rather than on their having run.
RULES: Final[tuple[dict, ...]] = tuple(
    [
        {
            "parameters": {
                "allowed_merge_methods": ["squash"],
                "dismiss_stale_reviews_on_push": True,
                "require_code_owner_review": True,
                "require_extra_approval_for_unattributed_changes": True,
                "require_last_push_approval": False,
                "required_approving_review_count": 0,
                "required_review_thread_resolution": True,
                "required_reviewers": [],
            },
            "ruleset_source": "Syndic/unnatural_designs",
            "ruleset_source_type": "Repository",
            "type": "pull_request",
        },
        {
            "parameters": {
                "code_scanning_tools": [
                    {
                        "alerts_threshold": "errors",
                        "security_alerts_threshold": "high_or_higher",
                        "tool": "CodeQL",
                    },
                    {
                        "alerts_threshold": "errors",
                        "security_alerts_threshold": "high_or_higher",
                        "tool": "Trivy",
                    },
                ]
            },
            "ruleset_source": "Syndic/unnatural_designs",
            "ruleset_source_type": "Repository",
            "type": "code_scanning",
        },
        {
            "parameters": {
                "do_not_enforce_on_create": True,
                "required_status_checks": [
                    {"context": "ADR number uniqueness check", "integration_id": 15368},
                    {"context": "Action self-test", "integration_id": 15368},
                    {"context": "Base image (all platforms)", "integration_id": 15368},
                    {"context": "Build and test (all targets)", "integration_id": 15368},
                    {"context": "Build devcontainer and smoke test", "integration_id": 15368},
                    {"context": "CodeQL Analysis (all languages)", "integration_id": 15368},
                    {"context": "Detect devcontainer changes", "integration_id": 15368},
                    {"context": "Gazelle BUILD file check", "integration_id": 15368},
                    {"context": "MODULE.bazel.lock freshness", "integration_id": 15368},
                    {"context": "Module completeness check", "integration_id": 15368},
                    {"context": "No-cgo policy check", "integration_id": 15368},
                    {"context": "Python version consistency check", "integration_id": 15368},
                    {
                        "context": "Repository constraint enforcement manifest consistency check",
                        "integration_id": 15368,
                    },
                    {"context": "Secrets check", "integration_id": 15368},
                    {"context": "Semgrep", "integration_id": 15368},
                    {"context": "Trivy", "integration_id": 15368},
                    {"context": "go.work consistency/completeness check", "integration_id": 15368},
                    {"context": "golangci-lint (all modules)", "integration_id": 15368},
                    {"context": "govulncheck (all modules)", "integration_id": 15368},
                    {"context": "pip-audit", "integration_id": 15368},
                    {"context": "ruff", "integration_id": 15368},
                    {"context": "shellcheck", "integration_id": 15368},
                    {"context": "ty", "integration_id": 15368},
                ],
                "strict_required_status_checks_policy": True,
            },
            "ruleset_source": "Syndic/unnatural_designs",
            "ruleset_source_type": "Repository",
            "type": "required_status_checks",
        },
        {
            "parameters": {"review_draft_pull_requests": False, "review_on_push": True},
            "ruleset_source": "Syndic/unnatural_designs",
            "ruleset_source_type": "Repository",
            "type": "copilot_code_review",
        },
        {
            "parameters": {"severity": "errors"},
            "ruleset_source": "Syndic/unnatural_designs",
            "ruleset_source_type": "Repository",
            "type": "code_quality",
        },
        {
            "ruleset_source": "Syndic/unnatural_designs",
            "ruleset_source_type": "Repository",
            "type": "creation",
        },
        {
            "ruleset_source": "Syndic/unnatural_designs",
            "ruleset_source_type": "Repository",
            "type": "deletion",
        },
        {
            "ruleset_source": "Syndic/unnatural_designs",
            "ruleset_source_type": "Repository",
            "type": "non_fast_forward",
        },
        {
            "ruleset_source": "Syndic/unnatural_designs",
            "ruleset_source_type": "Repository",
            "type": "required_linear_history",
        },
        {
            "ruleset_source": "Syndic/unnatural_designs",
            "ruleset_source_type": "Repository",
            "type": "required_signatures",
        },
    ]
)


def required_check_names() -> frozenset[str]:
    """The check names the rules require, projected out of RULES.

    Raises rather than returning an empty set when no `required_status_checks` rule is present: a
    manifest that requires nothing is not a manifest whose consumers should quietly pass.
    """
    for rule in RULES:
        if rule["type"] == "required_status_checks":
            contexts = rule["parameters"]["required_status_checks"]
            return frozenset(entry["context"] for entry in contexts)
    raise AssertionError("RULES carries no `required_status_checks` rule")


# Deliberately not binding, one reason each. This is the load-bearing list: it is where "this does
# not block a merge" becomes a decision someone wrote down instead of an omission nobody made on
# purpose. Membership is decided statically, by workflow trigger, so a job that never runs on an
# ordinary PR still needs an entry — and saying why is the only place that fact is recorded.
NOT_REQUIRED: Final = {
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
FAN_IN_EXEMPTIONS: Final = (
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
