"""Tests for classify_changed_paths.py.

Scoped to the script: the pure functions (is_branch_creation, select, classify, format_outputs)
carry all the non-I/O logic, and the git diff and $GITHUB_OUTPUT wiring is exercised end-to-end by
the caller workflows on real PRs.
"""

import unittest

from meta.scripts.classify_changed_paths import (
    classify,
    format_outputs,
    is_branch_creation,
    select,
)
from meta.scripts.path_classification_pattern_sets import SETS

# Synthetic on purpose: see the module docstring. Three sets, one overlapping path, so an
# independence bug shows up as a wrong pairing rather than a wrong single flag. `beta` carries two
# patterns, since a set holding more than one is the normal case.
_SETS = {"alpha": (r"^a/",), "beta": (r"^b/", r"^shared$"), "gamma": (r"^shared$",)}


class TestIsBranchCreation(unittest.TestCase):
    def test_all_zeros_short(self):
        self.assertTrue(is_branch_creation("0000000"))

    def test_all_zeros_full_sha(self):
        self.assertTrue(is_branch_creation("0" * 40))

    def test_real_sha(self):
        self.assertFalse(is_branch_creation("1bc92be0000"))

    def test_branch_ref(self):
        self.assertFalse(is_branch_creation("origin/main"))

    def test_empty(self):
        # An empty base is not a branch-creation sentinel; let the diff surface the error.
        self.assertFalse(is_branch_creation(""))


class TestClassify(unittest.TestCase):
    def test_every_set_is_reported_not_just_the_hits(self):
        # The callers read `steps.<id>.outputs.<name>`, so a set that did not fire has to be
        # present and false rather than absent.
        self.assertEqual(classify(["a/x"], _SETS), {"alpha": True, "beta": False, "gamma": False})

    def test_sets_are_independent(self):
        self.assertEqual(classify(["shared"], _SETS), {"alpha": False, "beta": True, "gamma": True})

    def test_any_matching_file_is_enough(self):
        self.assertEqual(
            classify(["README.md", "b/y"], _SETS),
            {"alpha": False, "beta": True, "gamma": False},
        )

    def test_no_files(self):
        self.assertEqual(classify([], _SETS), {"alpha": False, "beta": False, "gamma": False})

    def test_no_sets(self):
        self.assertEqual(classify(["a/x"], {}), {})

    def test_patterns_self_anchor(self):
        # `re.search`, so an unanchored pattern matches anywhere; the anchoring belongs to the
        # pattern, not to this function.
        self.assertEqual(classify(["deep/a/x"], {"alpha": (r"^a/",)}), {"alpha": False})
        self.assertEqual(classify(["deep/a/x"], {"alpha": (r"a/",)}), {"alpha": True})


class TestSelect(unittest.TestCase):
    """`select` is the fail-closed guard on `--emit`, so its refusal is the thing under test.

    Unlike the rest of this suite it uses the real `SETS`, because what it does is resolve a
    caller's name against them. It reads the shared sets but asserts nothing about their contents;
    that is `test_path_classification_pattern_sets.py`'s job.
    """

    def test_returns_the_named_sets_in_the_order_asked_for(self):
        # The order reaches $GITHUB_OUTPUT, which keeps a run's log readable against the workflow
        # that produced it.
        self.assertEqual(list(select(["changed", "base"])), ["changed", "base"])

    def test_the_patterns_are_the_shared_ones(self):
        self.assertEqual(select(["base"])["base"], SETS["base"])

    def test_an_unknown_name_is_refused(self):
        # Not an empty set. A caller naming a set that does not exist would otherwise emit
        # `name=false` on every run and gate its steps off forever — the base image would quietly
        # stop being published and every check would stay green. Simplifying this to a
        # `SETS.get(name, ())` would pass every other test in the repo.
        with self.assertRaises(SystemExit):
            select(["base", "nope"])

    def test_the_refusal_names_every_missing_set_not_just_the_first(self):
        # The message is the whole diagnosis: the caller is a workflow, so nobody is at a REPL to
        # go looking. Fixture names must not be substrings of one another — an earlier pair
        # ("nope"/"alsonope") let a first-only message satisfy both assertions, so this test passed
        # on exactly the regression it exists to catch.
        with self.assertRaises(SystemExit) as caught:
            select(["ghost", "base", "phantom"])
        self.assertIn("ghost", str(caught.exception))
        self.assertIn("phantom", str(caught.exception))

    def test_no_names_selects_nothing(self):
        self.assertEqual(select([]), {})


class TestFormatOutputs(unittest.TestCase):
    def test_render(self):
        self.assertEqual(
            format_outputs({"python": True, "bazel": False}), "python=true\nbazel=false\n"
        )

    def test_order_follows_the_mapping(self):
        # The caller's `--emit` order reaches $GITHUB_OUTPUT unchanged, which keeps a run's log
        # readable against the workflow that produced it.
        self.assertEqual(format_outputs({"b": False, "a": False}), "b=false\na=false\n")

    def test_empty(self):
        self.assertEqual(format_outputs({}), "")


if __name__ == "__main__":
    unittest.main()
