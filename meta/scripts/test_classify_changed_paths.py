"""Tests for classify_changed_paths.py.

Scoped to the script: the pure functions (is_branch_creation, classify, format_outputs) carry all
the non-I/O logic, and the git diff and $GITHUB_OUTPUT wiring is exercised end-to-end by the
caller workflows on real PRs.

Deliberately nothing about *which* paths are in a rule set. That used to live here, read back out
of the workflows' `--rule` arguments so the two copies could be held together — a job this suite
should never have had. The rules are defined once in `.github/path-rules.toml` now, and
`test_path_rules.py` covers them. `classify` is a regex loop and does not care where its patterns
came from, so the fixtures below are synthetic.
"""

import unittest

from meta.scripts.classify_changed_paths import (
    classify,
    format_outputs,
    is_branch_creation,
)

# Synthetic on purpose: see the module docstring. Two groups, one overlapping path, so an
# independence bug shows up as a wrong pairing rather than a wrong single flag.
_RULES = {"alpha": r"^a/", "beta": r"^b/|^shared$", "gamma": r"^shared$"}


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
    def test_every_group_is_reported_not_just_the_hits(self):
        # The callers read `steps.<id>.outputs.<name>`, so a group that did not fire has to be
        # present and false rather than absent.
        self.assertEqual(classify(["a/x"], _RULES), {"alpha": True, "beta": False, "gamma": False})

    def test_groups_are_independent(self):
        self.assertEqual(
            classify(["shared"], _RULES), {"alpha": False, "beta": True, "gamma": True}
        )

    def test_any_matching_file_is_enough(self):
        self.assertEqual(
            classify(["README.md", "b/y"], _RULES),
            {"alpha": False, "beta": True, "gamma": False},
        )

    def test_no_files(self):
        self.assertEqual(classify([], _RULES), {"alpha": False, "beta": False, "gamma": False})

    def test_no_rules(self):
        self.assertEqual(classify(["a/x"], {}), {})

    def test_patterns_self_anchor(self):
        # `re.search`, so an unanchored pattern matches anywhere; the anchoring belongs to the
        # rule, not to this function.
        self.assertEqual(classify(["deep/a/x"], {"alpha": r"^a/"}), {"alpha": False})
        self.assertEqual(classify(["deep/a/x"], {"alpha": r"a/"}), {"alpha": True})


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
