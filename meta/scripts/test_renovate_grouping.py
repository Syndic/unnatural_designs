"""Holds the Renovate rules whose correctness depends on where they sit in the file.

Renovate merges every matching `packageRule` in order and the last writer wins. The three `major`
grouping rules are scoped so they cannot overlap the minor/patch catch-all, so their order is free.
`Go language version` is the exception: it matches a subset of what the catch-all matches, so it
only takes effect while it appears *after* it.

That is worth a test rather than a comment because the failure is silent and slow. A reorder --
an editor sorting keys, a merge resolving oddly, someone tidying the file -- puts Go minors back
into the batch with no error anywhere. Nothing would surface it until a Go minor landed inside a
fifteen-dependency PR and took the CodeQL job red with it, which is the arrangement this rule
exists to prevent and happens about twice a year.

Why the overlap is there at all, rather than being designed out: Go *patch* bumps stay in the batch
while Go *minor* bumps leave it, and no single rule expresses "minor-or-patch except Go minor".
See README's "Dependency updates" and .claude/CLAUDE.md "CodeQL runs as advanced setup".

The second rule is the one disabling the devcontainer base-image *pin*. Same ordering hazard, and
the same silence: ahead of the catch-all it is overwritten, and Renovate starts proposing bumps to
a derived file that three other callers already keep current. Not to be confused with the
`devcontainer base image` group, which is a different dependency — the upstream Debian base the
image is assembled over, which Renovate is *supposed* to bump.
"""

import json
import unittest
from pathlib import Path

# Not .resolve(): the file is a data dep and lives in the runfiles tree beside this one.
_ROOT = Path(__file__).parent.parent.parent
_RENOVATE = _ROOT / "renovate.json"

_GO_MINOR_GROUP = "Go language version"
_CATCH_ALL_GROUP = "all non-major dependencies"

# The image consumers `FROM`. A derived file here: `sync_base_image_pin.py` writes it, the
# `base-image-pin` hook and `renovate-derived-files.yml` re-derive it, and
# `//.devcontainer:test_base_image_pin` checks it. Renovate has no part to play.
_PIN_PACKAGE = "ghcr.io/syndic/unnatural_designs-devcontainer-base"


def package_rules() -> list[dict]:
    return json.loads(_RENOVATE.read_text(encoding="utf-8"))["packageRules"]


def index_of_group(rules: list[dict], group: str) -> int:
    matches = [i for i, rule in enumerate(rules) if rule.get("groupName") == group]
    if len(matches) != 1:
        raise AssertionError(f"expected exactly one `{group}` rule in renovate.json, got {matches}")
    return matches[0]


def index_of_pin_rule(rules: list[dict]) -> int:
    matches = [i for i, rule in enumerate(rules) if rule.get("matchPackageNames") == [_PIN_PACKAGE]]
    if len(matches) != 1:
        raise AssertionError(
            f"expected exactly one rule matching `{_PIN_PACKAGE}` in renovate.json, got {matches}"
        )
    return matches[0]


class GoMinorGroupingTest(unittest.TestCase):
    def setUp(self):
        self.rules = package_rules()

    def test_go_minor_has_its_own_group(self):
        rule = self.rules[index_of_group(self.rules, _GO_MINOR_GROUP)]
        self.assertEqual(rule.get("matchDepNames"), ["go"])
        self.assertEqual(
            rule.get("matchUpdateTypes"),
            ["minor"],
            "patch bumps stay in the batch — the extractor's language-version gate moves on "
            "minors, and pulling patches out would cost a PR a fortnight for nothing",
        )

    def test_go_minor_is_ordered_after_the_catch_all(self):
        """The whole rule is inert if this is false, and nothing else would say so."""
        self.assertGreater(
            index_of_group(self.rules, _GO_MINOR_GROUP),
            index_of_group(self.rules, _CATCH_ALL_GROUP),
            "Renovate's last matching rule wins, so a `Go language version` rule ahead of the "
            f"`{_CATCH_ALL_GROUP}` catch-all is overwritten by it and Go minors go back into the "
            "batch — silently, since neither Renovate nor CI has anything to complain about",
        )

    def test_the_catch_all_still_matches_go_patches(self):
        """The overlap is deliberate and one-sided; narrowing the catch-all would undo it."""
        catch_all = self.rules[index_of_group(self.rules, _CATCH_ALL_GROUP)]
        self.assertEqual(sorted(catch_all.get("matchUpdateTypes", [])), ["minor", "patch"])
        self.assertNotIn(
            "matchDepNames",
            catch_all,
            "scoping the catch-all away from `go` would pull Go patch bumps out of the batch too",
        )


class BaseImagePinDisabledTest(unittest.TestCase):
    """The pin is a derived file, so a Renovate PR against it is noise fighting three other
    callers. The rule that says so is subject to the same last-writer-wins ordering as the Go one.
    """

    def setUp(self):
        self.rules = package_rules()

    def test_the_consumer_pin_is_disabled(self):
        rule = self.rules[index_of_pin_rule(self.rules)]
        self.assertIs(
            rule.get("enabled"),
            False,
            "the pin is reproducible from the tree, so a bump PR could only ever restate what the "
            "tree already determines, one publish later",
        )
        self.assertEqual(rule.get("matchDatasources"), ["docker"])

    def test_the_disable_is_ordered_after_the_catch_all(self):
        """The whole rule is inert if this is false, and nothing else would say so."""
        self.assertGreater(
            index_of_pin_rule(self.rules),
            index_of_group(self.rules, _CATCH_ALL_GROUP),
            "Renovate's last matching rule wins, so a disable ahead of the "
            f"`{_CATCH_ALL_GROUP}` catch-all is overwritten by it and Renovate resumes proposing "
            "digest bumps to a derived file",
        )

    def test_the_upstream_base_group_is_a_different_dependency(self):
        """Guards the confusion this pair invites: one is disabled, the other is deliberate."""
        upstream = self.rules[index_of_group(self.rules, "devcontainer base image")]
        self.assertEqual(upstream.get("matchManagers"), ["bazel-module"])
        self.assertEqual(upstream.get("matchDepTypes"), ["oci_pull"])
        self.assertNotIn(
            "matchPackageNames",
            upstream,
            "this group is the upstream base in MODULE.bazel, not the pin consumers FROM",
        )


if __name__ == "__main__":
    unittest.main()
