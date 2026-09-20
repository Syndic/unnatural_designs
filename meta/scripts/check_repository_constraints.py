#!/usr/bin/env python3
"""Holds the enforcement manifest to what GitHub reports for the protected branch.

`ci_enforcement_manifest.py` is a claim about repo settings, and every other guard over it is
tree-local: they hold the workflows to the claim without being able to ask whether the claim is
true. This is the one that asks. It reads the rules effectively applying to the branch and fails
when they and the manifest disagree in either direction.

Read as the *effective* rules on the branch — the union of whatever rulesets reach it — rather
than as a ruleset named by id. A read keyed on one id answers "what is in ruleset N", which equals
"what gates a merge" only while that is the only ruleset targeting the branch; add a second and the
read reports green while the gate moved. `meta/docs/adr/0003-...` carries that decision, and the
two blind spots it accepts.

Nothing here writes the manifest, and nothing should be added that does. A guard that can update
the claim to match reality has stopped being a guard.

**Every unreadable answer is a failure, never a pass.** A `4xx` means the token lost a permission
or the repository moved, which is exactly when a guard that shrugged would be worthless, so it
fails at once. A `5xx` or a timeout is noise and is retried a bounded number of times before
failing anyway. A `200` whose body is not the expected shape fails without being retried.

Usage: ./meta/scripts/check_repository_constraints.py
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

# When invoked as `python3 meta/scripts/check_repository_constraints.py` (the form used in CI),
# the workspace root is not on sys.path, so `from meta.scripts.X` would fail.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from meta.scripts._workspace import col_range, exit_status, workspace_root
from meta.scripts.ci_enforcement_manifest import IGNORED_RULE_FIELDS, PROTECTED_BRANCH, RULES

# Where a finding is reported, since the manifest is the half of the disagreement a human edits.
MANIFEST = Path("meta/scripts") / "ci_enforcement_manifest.py"
_ANCHOR = "RULES"

_ENDPOINT = "https://api.github.com/repos/{repo}/rules/branches/{branch}"
_API_VERSION = "2022-11-28"

# Three attempts over roughly six seconds. The budget exists to absorb a blip, not to wait out an
# outage: a longer one would hold a required check open while telling a reader nothing it does not
# already know after the second failure.
_ATTEMPTS = 3
_BACKOFF_SECONDS = 2.0
_TIMEOUT_SECONDS = 30

_STATUS_CHECKS = "required_status_checks"


class UnreadableRules(Exception):
    """The API did not yield rules that can be compared.

    Distinct from a disagreement: a disagreement is an answer, and this is the absence of one.
    Both fail the guard, and keeping them separate is what stops "could not check" from being
    reported as "checked, and fine".
    """


def ordering_key(value: Any) -> str:
    """A total order over JSON values, so a list can be sorted without knowing what is in it."""
    return json.dumps(value, sort_keys=True)


def canonical(value: Any) -> Any:
    """`value` with every mapping key-sorted and every list put in a canonical order.

    Collections compare by content, not by position: the API returns rules and contexts in storage
    order, which shifts when an entry is added or removed and which no invariant here depends on.
    No list in these rules is ordered meaningfully — contexts, merge methods, reviewers and
    scanning tools are all sets wearing a list's clothes — so this is applied everywhere rather
    than to a hand-listed few, which would go stale the first time GitHub adds another.
    """
    if isinstance(value, dict):
        return {key: canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return sorted((canonical(item) for item in value), key=ordering_key)
    return value


def comparable(rules: Any) -> list[dict]:
    """`rules` with the ignored fields dropped and everything else canonicalised.

    Raises rather than coercing: a body that is not a list of typed rule objects is the shape a
    token without the permission, or an endpoint that changed, would produce.
    """
    if not isinstance(rules, list):
        raise UnreadableRules(f"expected a list of rules, got {type(rules).__name__}")
    stripped = []
    for rule in rules:
        if not isinstance(rule, dict):
            raise UnreadableRules(f"a rule is not an object: {rule!r}")
        if "type" not in rule:
            raise UnreadableRules(f"a rule carries no `type`: {rule!r}")
        stripped.append({k: v for k, v in rule.items() if k not in IGNORED_RULE_FIELDS})
    if not any(rule["type"] == _STATUS_CHECKS for rule in stripped):
        raise UnreadableRules(
            f"no `{_STATUS_CHECKS}` rule came back. Either the branch enforces none — which no "
            "manifest should pass over quietly — or the token cannot see them"
        )
    return canonical(stripped)


def _by_type(rules: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for rule in rules:
        grouped.setdefault(rule["type"], []).append(rule)
    return grouped


def _contexts(rule: dict) -> set[str]:
    entries = (rule.get("parameters") or {}).get(_STATUS_CHECKS) or []
    return {entry["context"] for entry in entries if isinstance(entry, dict) and "context" in entry}


def _without_contexts(rule: dict) -> dict:
    """`rule` with the context list removed, so the rest of it can be compared separately."""
    parameters = {k: v for k, v in (rule.get("parameters") or {}).items() if k != _STATUS_CHECKS}
    return {**rule, "parameters": parameters}


def _status_check_differences(claimed: dict, enforced: dict) -> list[str]:
    """Name the contexts that moved, rather than printing two lists and leaving the diff to a human.

    This is the rule that changes, so it is the one worth spelling out. Its other parameters still
    compare whole, by the caller, and only when they are what differs — a membership change
    reported once by name beats the same change reported twice with a dump of all 23 around it.
    """
    found = []
    ours, theirs = _contexts(claimed), _contexts(enforced)
    for context in sorted(ours - theirs):
        found.append(
            f"the manifest requires `{context}`, which the repository does not. Either the check "
            "was dropped from the rules, or the manifest was written ahead of the settings"
        )
    for context in sorted(theirs - ours):
        found.append(
            f"the repository requires `{context}`, which the manifest does not claim. A check "
            "became a merge gate without being written down"
        )
    return found


def differences(claimed: list[dict], enforced: list[dict]) -> list[str]:
    """Every way the two disagree, in both directions.

    Both directions matter and for different reasons. A rule enforced but unclaimed means the gate
    moved without anyone recording it; a rule claimed but unenforced means the manifest — which the
    rest of the guards trust — is asserting a gate that is not there.
    """
    found = []
    ours, theirs = _by_type(claimed), _by_type(enforced)

    for rule_type in sorted(set(ours) | set(theirs)):
        # Absence is the empty list rather than None, which is what every branch below already
        # treats it as. `rule_type` comes from the union of both key sets, so both being empty is
        # unreachable — but saying so in the types beats asserting it in a comment.
        mine, yours = ours.get(rule_type, []), theirs.get(rule_type, [])
        if mine and not yours:
            found.append(
                f"the manifest claims a `{rule_type}` rule that the repository does not enforce"
            )
            continue
        if yours and not mine:
            found.append(
                f"the repository enforces a `{rule_type}` rule the manifest does not claim"
            )
            continue
        if mine == yours:
            continue
        if rule_type == _STATUS_CHECKS and len(mine) == len(yours) == 1:
            found.extend(_status_check_differences(mine[0], yours[0]))
            if _without_contexts(mine[0]) == _without_contexts(yours[0]):
                continue
        found.append(
            f"the `{rule_type}` rule differs.\n"
            f"    manifest:   {ordering_key(mine)}\n"
            f"    repository: {ordering_key(yours)}"
        )
    return found


def fetch_rules(
    repo: str,
    branch: str,
    token: str,
    opener: Callable[..., Any] = urllib.request.urlopen,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """The rules GitHub reports for `branch`, or raise.

    `opener` and `sleep` are injected so the retry policy is exercised by tests rather than
    described by a comment. The endpoint needs only `Metadata: read`, which an Actions token
    carries unconditionally — see the module docstring for why this one and not the ruleset read.
    """
    # S310 is suppressed rather than worked around: the scheme is fixed in `_ENDPOINT`, and the
    # only interpolated values are the repository and branch, neither of which can reach it.
    request = urllib.request.Request(  # noqa: S310
        _ENDPOINT.format(repo=repo, branch=branch),
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": _API_VERSION,
        },
    )

    for attempt in range(1, _ATTEMPTS + 1):
        try:
            with opener(request, timeout=_TIMEOUT_SECONDS) as response:
                body = response.read()
            break
        except urllib.error.HTTPError as error:
            # A 4xx is the signal, not the noise: the token lost a permission, or the repository
            # moved. Retrying only delays discovering it.
            if error.code < 500:
                raise UnreadableRules(
                    f"GitHub answered {error.code} {error.reason}. This is a permissions or "
                    "addressing failure rather than a blip, so it is not retried"
                ) from error
            if attempt == _ATTEMPTS:
                raise UnreadableRules(
                    f"GitHub answered {error.code} {error.reason} on {_ATTEMPTS} attempts"
                ) from error
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt == _ATTEMPTS:
                raise UnreadableRules(
                    f"could not reach GitHub on {_ATTEMPTS} attempts: {error}"
                ) from error
        sleep(_BACKOFF_SECONDS * attempt)

    try:
        return json.loads(body)
    except json.JSONDecodeError as error:
        raise UnreadableRules(f"GitHub's answer was not JSON: {error}") from error


def _problem(root: Path, message: str) -> str:
    """`file:line:start-end: message`, the shape .vscode/tasks.json's problem matcher parses."""
    manifest = root / MANIFEST
    lineno = 1
    for number, line in enumerate(manifest.read_text().splitlines(), start=1):
        if line.startswith(_ANCHOR):
            lineno = number
            break
    start, end = col_range(manifest, lineno, _ANCHOR)
    return f"{MANIFEST}:{lineno}:{start}-{end}: {message}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPOSITORY"),
        help="owner/name of the repository to read (default: $GITHUB_REPOSITORY)",
    )
    parser.add_argument(
        "--branch",
        default=PROTECTED_BRANCH,
        help=f"branch whose rules to read (default: {PROTECTED_BRANCH})",
    )
    args = parser.parse_args(argv)

    root = workspace_root()
    token = os.environ.get("GITHUB_TOKEN", "")
    if not args.repo or not token:
        missing = "GITHUB_REPOSITORY/--repo" if not args.repo else "GITHUB_TOKEN"
        print(_problem(root, f"{missing} is unset, so the rules cannot be read"))
        return exit_status(1)

    try:
        enforced = comparable(fetch_rules(args.repo, args.branch, token))
        found = differences(comparable(list(RULES)), enforced)
    except UnreadableRules as error:
        print(_problem(root, f"the repository's rules could not be read: {error}"))
        return exit_status(1)

    for message in found:
        print(_problem(root, message))
    if found:
        print(
            f"\n{len(found)} disagreement(s) between the manifest and `{args.branch}`. "
            "Nothing here rewrites the manifest: decide which side is wrong, then either change "
            "the repository's rules or edit RULES so the claim matches what you meant.",
            file=sys.stderr,
        )
    return exit_status(len(found))


if __name__ == "__main__":
    raise SystemExit(main())
