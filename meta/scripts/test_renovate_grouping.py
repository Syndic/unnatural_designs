"""Holds the one Renovate grouping rule whose correctness depends on where it sits in the file.

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
"""

import json
import unittest
from pathlib import Path

# Not .resolve(): the file is a data dep and lives in the runfiles tree beside this one.
_ROOT = Path(__file__).parent.parent.parent
_RENOVATE = _ROOT / "renovate.json"

_GO_MINOR_GROUP = "Go language version"
_CATCH_ALL_GROUP = "all non-major dependencies"


def package_rules() -> list[dict]:
    return json.loads(_RENOVATE.read_text(encoding="utf-8"))["packageRules"]


def index_of_group(rules: list[dict], group: str) -> int:
    matches = [i for i, rule in enumerate(rules) if rule.get("groupName") == group]
    if len(matches) != 1:
        raise AssertionError(f"expected exactly one `{group}` rule in renovate.json, got {matches}")
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


if __name__ == "__main__":
    unittest.main()
