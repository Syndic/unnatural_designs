"""Holds the Renovate rules whose correctness depends on where they sit in the file.

Renovate merges every matching `packageRule` in order and the last writer wins. The three `major`
grouping rules are scoped so they cannot overlap the minor/patch catch-all, so their order is free.
A grouping rule that *does* overlap it -- today `Go language version` and `Python language version`
-- only takes effect while it appears *after* the catch-all.

That is worth a test rather than a comment because the failure is silent and slow. A reorder --
an editor sorting keys, a merge resolving oddly, someone tidying the file -- puts a toolchain minor
back into the batch with no error anywhere. Nothing would surface it until that minor landed inside
a fifteen-dependency PR and took a check red with it, as Python 3.15 did in #350.

The ordering check selects rules by shape rather than by name, so the next toolchain group is
covered without an edit here: any rule that sets a `groupName` and can match a `minor` or `patch`
update. A rule with no `matchUpdateTypes` matches every type, so it counts.

Why the overlap is there at all, rather than being designed out: a toolchain's *patch* bumps stay in
the batch while its *minor* bumps leave it, and no single rule expresses "minor-or-patch except a
toolchain's minor". See README's "Dependency updates" and .claude/CLAUDE.md "CodeQL runs as advanced
setup".

The second rule is the one disabling the devcontainer base-image *pin*. Same ordering hazard, and
the same silence: ahead of the catch-all it is overwritten, and Renovate starts proposing bumps to
a derived file that three other callers already keep current. Not to be confused with the
`devcontainer base image` group, which is a different dependency — the upstream Debian base the
image is assembled over, which Renovate is *supposed* to bump.
"""

import json
import unittest
from pathlib import Path
from typing import ClassVar

# Left unresolved because the file is a data dep and lives in the runfiles tree beside this one.
_ROOT = Path(__file__).parent.parent.parent
_RENOVATE = _ROOT / "renovate.json"

_CATCH_ALL_GROUP = "all non-major dependencies"

# What the catch-all batches; a grouping rule that can match either has to come after it.
_BATCHED_TYPES = frozenset({"minor", "patch"})

# Each toolchain minor's own group, the dep it isolates, and why its patches stay in the batch.
_TOOLCHAIN_MINOR_GROUPS = {
    "Go language version": (
        "go",
        "the extractor's language-version gate moves on minors, and pulling patches out would "
        "cost a PR a fortnight for nothing",
    ),
    "Python language version": (
        "python",
        "rules_python's coverage wheel table is keyed by minor (`cp314`), so a patch cannot trip "
        "it; every Python pin here is major.minor anyway, so Renovate proposes no patches",
    ),
}

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


def overlaps_the_catch_all(rule: dict) -> bool:
    """Whether `rule` is a grouping rule the catch-all would overwrite if it came first."""
    if "groupName" not in rule or rule["groupName"] == _CATCH_ALL_GROUP:
        return False
    types = rule.get("matchUpdateTypes")
    return types is None or bool(_BATCHED_TYPES & set(types))


def misordered_groups(rules: list[dict]) -> list[str]:
    """Every overlapping grouping rule placed ahead of the catch-all.

    Raises rather than returning `[]` when there is nothing to compare: no catch-all, or no rule
    that overlaps it. Either way an empty answer would be a pass that checked nothing.
    """
    catch_all = index_of_group(rules, _CATCH_ALL_GROUP)
    overlapping = [i for i, rule in enumerate(rules) if overlaps_the_catch_all(rule)]
    if not overlapping:
        raise AssertionError(
            f"no grouping rule overlaps the `{_CATCH_ALL_GROUP}` catch-all, so the ordering "
            "check has nothing to check"
        )
    return [rules[i]["groupName"] for i in overlapping if i < catch_all]


class GroupOrderingTest(unittest.TestCase):
    def setUp(self):
        self.rules = package_rules()

    def test_no_overlapping_group_is_ahead_of_the_catch_all(self):
        """Each such rule is inert while this is false, and nothing else would say so."""
        self.assertEqual(
            misordered_groups(self.rules),
            [],
            "Renovate's last matching rule wins, so a grouping rule ahead of the "
            f"`{_CATCH_ALL_GROUP}` catch-all is overwritten by it for minor and patch updates, "
            "and those go back into the batch — silently, since neither Renovate nor CI has "
            "anything to complain about",
        )

    def test_the_shape_finds_every_toolchain_minor_group(self):
        """The shape is the gate; this confirms it still sees the rules it exists for."""
        found = {rule["groupName"] for rule in self.rules if overlaps_the_catch_all(rule)}
        self.assertLessEqual(set(_TOOLCHAIN_MINOR_GROUPS), found)


class MisorderedGroupsTest(unittest.TestCase):
    """The predicate over synthetic rule lists, so each edge case is shown to fail."""

    _CATCH_ALL: ClassVar[dict] = {
        "matchUpdateTypes": ["minor", "patch"],
        "groupName": _CATCH_ALL_GROUP,
    }

    def test_a_minor_group_ahead_of_the_catch_all_is_reported(self):
        rules = [{"matchUpdateTypes": ["minor"], "groupName": "early"}, self._CATCH_ALL]
        self.assertEqual(misordered_groups(rules), ["early"])

    def test_a_group_with_no_update_types_counts_as_overlapping(self):
        rules = [{"matchDepNames": ["x"], "groupName": "untyped"}, self._CATCH_ALL]
        self.assertEqual(misordered_groups(rules), ["untyped"])

    def test_a_major_only_group_ahead_of_the_catch_all_is_fine(self):
        rules = [
            {"matchUpdateTypes": ["major"], "groupName": "majors"},
            self._CATCH_ALL,
            {"matchUpdateTypes": ["minor"], "groupName": "late"},
        ]
        self.assertEqual(misordered_groups(rules), [])

    def test_a_missing_catch_all_fails(self):
        with self.assertRaises(AssertionError):
            misordered_groups([{"matchUpdateTypes": ["minor"], "groupName": "late"}])

    def test_nothing_to_check_fails(self):
        with self.assertRaises(AssertionError):
            misordered_groups([self._CATCH_ALL, {"matchManagers": ["x"], "pinDigests": False}])


class ToolchainMinorGroupingTest(unittest.TestCase):
    def setUp(self):
        self.rules = package_rules()

    def test_each_toolchain_minor_has_its_own_group(self):
        for group, (dep, why_patches_stay) in _TOOLCHAIN_MINOR_GROUPS.items():
            with self.subTest(group=group):
                rule = self.rules[index_of_group(self.rules, group)]
                self.assertEqual(rule.get("matchDepNames"), [dep])
                self.assertEqual(
                    rule.get("matchUpdateTypes"),
                    ["minor"],
                    f"patches stay in the batch: {why_patches_stay}",
                )

    def test_the_catch_all_still_matches_toolchain_patches(self):
        """The overlap is deliberate and one-sided; narrowing the catch-all would undo it."""
        catch_all = self.rules[index_of_group(self.rules, _CATCH_ALL_GROUP)]
        self.assertEqual(sorted(catch_all.get("matchUpdateTypes", [])), ["minor", "patch"])
        self.assertNotIn(
            "matchDepNames",
            catch_all,
            "scoping the catch-all by dep name would pull toolchain patches out of the batch too",
        )


class BaseImagePinDisabledTest(unittest.TestCase):
    """The pin is a derived file, so a Renovate PR against it is noise fighting three other
    callers. The rule that says so is subject to the same last-writer-wins ordering as the
    language-version groups.
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
