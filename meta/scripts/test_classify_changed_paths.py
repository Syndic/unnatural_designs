"""Tests for classify_changed_paths.py.

The pure functions (parse_rule, parse_implication, is_branch_creation, classify,
format_outputs) carry all the non-I/O logic; tests focus there. The git diff and
$GITHUB_OUTPUT wiring is exercised end-to-end by the caller workflows on real PRs.

The two `RULES_*` fixtures below mirror the exact rules the workflows pass, so a regex
or coupling change in a workflow that isn't reflected here shows up as a test failure.
"""

import unittest

from meta.scripts.classify_changed_paths import (
    classify,
    format_outputs,
    is_branch_creation,
    parse_implication,
    parse_rule,
)

# The renovate-derived-files.yml rule set: Python manifests + MODULE.bazel, with the
# python→bazel coupling (requirements_lock.txt feeds pip.parse, whose hashes land in
# MODULE.bazel.lock's facts).
RULES_RENOVATE = {
    "python": r"(^|/)(pyproject\.toml|uv\.lock|requirements_lock\.txt)$",
    "bazel": r"(^|/)MODULE\.bazel$",
}
IMPLY_RENOVATE = [("python", "bazel")]

# The devcontainer.yml rule set: one group over .devcontainer/ and the workflow file.
RULES_DEVCONTAINER = {
    "changed": r"^\.devcontainer/|^\.github/workflows/devcontainer\.yml$",
}


class TestParseRule(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(parse_rule("python=foo.*bar"), ("python", "foo.*bar"))

    def test_regex_containing_equals(self):
        # Split on the FIRST '=', so a regex with '=' in it survives.
        self.assertEqual(parse_rule("k=a=b"), ("k", "a=b"))

    def test_missing_separator(self):
        with self.assertRaises(ValueError):
            parse_rule("noequals")

    def test_empty_name(self):
        with self.assertRaises(ValueError):
            parse_rule("=pattern")


class TestParseImplication(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(parse_implication("python=bazel"), ("python", "bazel"))

    def test_missing_target(self):
        with self.assertRaises(ValueError):
            parse_implication("python=")

    def test_missing_source(self):
        with self.assertRaises(ValueError):
            parse_implication("=bazel")


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


class TestClassifyRenovate(unittest.TestCase):
    def _run(self, files):
        return classify(files, RULES_RENOVATE, IMPLY_RENOVATE)

    def test_module_bazel_only(self):
        self.assertEqual(self._run(["MODULE.bazel"]), {"python": False, "bazel": True})

    def test_nested_module_bazel(self):
        # `$`-anchored, not whole-line: a nested MODULE.bazel still counts.
        self.assertEqual(self._run(["sub/mod/MODULE.bazel"]), {"python": False, "bazel": True})

    def test_module_bazel_lock_is_not_a_manifest(self):
        # The derived lock must NOT trigger a regen (nothing to regenerate from it).
        self.assertEqual(self._run(["MODULE.bazel.lock"]), {"python": False, "bazel": False})

    def test_requirements_lock_implies_bazel(self):
        self.assertEqual(self._run(["requirements_lock.txt"]), {"python": True, "bazel": True})

    def test_nested_pyproject_implies_bazel(self):
        self.assertEqual(self._run(["apps/foo/pyproject.toml"]), {"python": True, "bazel": True})

    def test_combined(self):
        self.assertEqual(self._run(["MODULE.bazel", "uv.lock"]), {"python": True, "bazel": True})

    def test_unrelated_only(self):
        self.assertEqual(self._run(["README.md", "go.mod"]), {"python": False, "bazel": False})

    def test_empty(self):
        self.assertEqual(self._run([]), {"python": False, "bazel": False})

    def test_decoy_module_bazel_template(self):
        self.assertEqual(self._run(["infra/MODULE.bazel.tmpl"]), {"python": False, "bazel": False})

    def test_decoy_uv_lock_backup(self):
        self.assertEqual(self._run(["uv.lock.bak"]), {"python": False, "bazel": False})


class TestClassifyDevcontainer(unittest.TestCase):
    def _run(self, files):
        return classify(files, RULES_DEVCONTAINER, [])

    def test_devcontainer_dir(self):
        self.assertEqual(self._run([".devcontainer/Dockerfile"]), {"changed": True})

    def test_workflow_file(self):
        self.assertEqual(self._run([".github/workflows/devcontainer.yml"]), {"changed": True})

    def test_other_workflow_not_matched(self):
        # The `$` on the second alternative keeps a sibling workflow from matching.
        self.assertEqual(self._run([".github/workflows/ci.yml"]), {"changed": False})

    def test_unrelated(self):
        self.assertEqual(self._run(["README.md"]), {"changed": False})


class TestClassifyImplications(unittest.TestCase):
    def test_no_implication_leaves_groups_independent(self):
        # Without the coupling, a Python-only change does not set bazel.
        self.assertEqual(
            classify(["uv.lock"], RULES_RENOVATE, []), {"python": True, "bazel": False}
        )

    def test_implication_reaches_fixpoint_through_a_chain(self):
        rules = {"a": r"^a$", "b": r"^b$", "c": r"^c$"}
        result = classify(["a"], rules, [("a", "b"), ("b", "c")])
        self.assertEqual(result, {"a": True, "b": True, "c": True})


class TestFormatOutputs(unittest.TestCase):
    def test_render(self):
        self.assertEqual(
            format_outputs({"python": True, "bazel": False}), "python=true\nbazel=false\n"
        )

    def test_empty(self):
        self.assertEqual(format_outputs({}), "")


if __name__ == "__main__":
    unittest.main()
