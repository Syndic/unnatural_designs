"""Tests for classify_changed_paths.py.

The pure functions (parse_rule, is_branch_creation, classify, format_outputs) carry all the
non-I/O logic; tests focus there. The git diff and $GITHUB_OUTPUT wiring is exercised
end-to-end by the caller workflows on real PRs.

The two `RULES_*` fixtures below mirror the exact rules the workflows pass, so a regex
or coupling change in a workflow that isn't reflected here shows up as a test failure.
"""

import unittest

from meta.scripts.classify_changed_paths import (
    classify,
    format_outputs,
    is_branch_creation,
    parse_rule,
)

# Mirrors renovate-derived-files.yml. `python`, `bazel`, and `go` are independent here: the
# python→bazel refresh is conditional (on requirements_lock.txt actually moving) and lives
# in the workflow, not in classification. `bazel` covers both inputs that invalidate
# MODULE.bazel.lock — MODULE.bazel (module graph) and .bazelversion (lock format). `go`
# matches the Go manifests only — go.sum / go.work.sum are derived (tidy/sync), so a Renovate
# Go bump is caught via its go.mod edit.
RULES_RENOVATE = {
    "python": r"(^|/)(pyproject\.toml|uv\.lock|requirements_lock\.txt)$",
    "bazel": r"(^|/)(MODULE\.bazel|\.bazelversion)$",
    "go": r"(^|/)(go\.mod|go\.work)$",
}

# Mirrors devcontainer.yml.
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
        return classify(files, RULES_RENOVATE)

    def test_module_bazel_only(self):
        self.assertEqual(self._run(["MODULE.bazel"]), {"python": False, "bazel": True, "go": False})

    def test_nested_module_bazel(self):
        # `$`-anchored, not whole-line: a nested MODULE.bazel still counts.
        self.assertEqual(
            self._run(["sub/mod/MODULE.bazel"]), {"python": False, "bazel": True, "go": False}
        )

    def test_bazelversion_triggers_bazel_refresh(self):
        # A bazel version bump changes the lock's `lockFileVersion`, so it needs the same
        # `bazel mod deps` refresh as a MODULE.bazel change — same output, one gate.
        self.assertEqual(
            self._run([".bazelversion"]), {"python": False, "bazel": True, "go": False}
        )

    def test_bazelversion_alone_does_not_trigger_python_or_go(self):
        # A .bazelversion-only PR must skip uv setup and the Go tidy entirely.
        result = self._run([".bazelversion"])
        self.assertFalse(result["python"])
        self.assertFalse(result["go"])

    def test_decoy_bazelversion_suffix(self):
        # `$`-anchored: a sibling like .bazelversion.bak must not trigger a refresh.
        self.assertEqual(
            self._run([".bazelversion.bak"]), {"python": False, "bazel": False, "go": False}
        )

    def test_module_bazel_lock_is_not_a_manifest(self):
        # The derived lock must NOT trigger a regen (nothing to regenerate from it).
        self.assertEqual(
            self._run(["MODULE.bazel.lock"]), {"python": False, "bazel": False, "go": False}
        )

    def test_requirements_lock_is_python_not_bazel(self):
        # Classification is independent: whether a Python change refreshes the Bazel lock
        # is decided later, on whether requirements_lock.txt actually moved.
        self.assertEqual(
            self._run(["requirements_lock.txt"]), {"python": True, "bazel": False, "go": False}
        )

    def test_nested_pyproject(self):
        self.assertEqual(
            self._run(["apps/foo/pyproject.toml"]), {"python": True, "bazel": False, "go": False}
        )

    def test_nested_go_mod(self):
        # A Renovate Go bump edits a per-module go.mod; the `(^|/)` prefix catches it at depth.
        self.assertEqual(
            self._run(["tools/net/go.mod"]), {"python": False, "bazel": False, "go": True}
        )

    def test_go_work(self):
        # go.work carries the go directive + workspace member list.
        self.assertEqual(self._run(["go.work"]), {"python": False, "bazel": False, "go": True})

    def test_go_sum_is_derived_not_a_trigger(self):
        # go.sum is regenerated by tidy; it must not be the sole reason the workflow fires
        # (a Renovate bump always moves go.mod alongside it, which does trigger).
        self.assertEqual(
            self._run(["tools/net/go.sum"]), {"python": False, "bazel": False, "go": False}
        )

    def test_go_work_sum_is_derived_not_a_trigger(self):
        # `go\.work$` is end-anchored, so go.work.sum (the derived workspace sum) does not match.
        self.assertEqual(self._run(["go.work.sum"]), {"python": False, "bazel": False, "go": False})

    def test_combined(self):
        self.assertEqual(
            self._run(["MODULE.bazel", "uv.lock"]), {"python": True, "bazel": True, "go": False}
        )

    def test_combined_go_python_bazel(self):
        # A grouped Renovate PR can touch all three ecosystems at once; every flag fires and
        # all derived files ride the workflow's single commit.
        self.assertEqual(
            self._run(["MODULE.bazel", "uv.lock", "tools/net/go.mod"]),
            {"python": True, "bazel": True, "go": True},
        )

    def test_unrelated_only(self):
        self.assertEqual(self._run(["README.md"]), {"python": False, "bazel": False, "go": False})

    def test_empty(self):
        self.assertEqual(self._run([]), {"python": False, "bazel": False, "go": False})

    def test_decoy_module_bazel_template(self):
        self.assertEqual(
            self._run(["infra/MODULE.bazel.tmpl"]), {"python": False, "bazel": False, "go": False}
        )

    def test_decoy_uv_lock_backup(self):
        self.assertEqual(self._run(["uv.lock.bak"]), {"python": False, "bazel": False, "go": False})

    def test_decoy_go_mod_backup(self):
        # Trailing suffix after go.mod must not match the `$`-anchored rule.
        self.assertEqual(
            self._run(["tools/net/go.mod.bak"]), {"python": False, "bazel": False, "go": False}
        )


class TestClassifyDevcontainer(unittest.TestCase):
    def _run(self, files):
        return classify(files, RULES_DEVCONTAINER)

    def test_devcontainer_dir(self):
        self.assertEqual(self._run([".devcontainer/Dockerfile"]), {"changed": True})

    def test_workflow_file(self):
        self.assertEqual(self._run([".github/workflows/devcontainer.yml"]), {"changed": True})

    def test_other_workflow_not_matched(self):
        # The `$` on the second alternative keeps a sibling workflow from matching.
        self.assertEqual(self._run([".github/workflows/ci.yml"]), {"changed": False})

    def test_unrelated(self):
        self.assertEqual(self._run(["README.md"]), {"changed": False})


class TestFormatOutputs(unittest.TestCase):
    def test_render(self):
        self.assertEqual(
            format_outputs({"python": True, "bazel": False}), "python=true\nbazel=false\n"
        )

    def test_empty(self):
        self.assertEqual(format_outputs({}), "")


if __name__ == "__main__":
    unittest.main()
