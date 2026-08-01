"""Tests for classify_changed_paths.py.

The pure functions (parse_rule, is_branch_creation, classify, format_outputs) carry all the
non-I/O logic; tests focus there. The git diff and $GITHUB_OUTPUT wiring is exercised
end-to-end by the caller workflows on real PRs.

The two `RULES_*` fixtures are read out of the workflows themselves rather than restated here.
A hand-copy is green whichever side drifts, and both sides move in the same PRs; each rule's
own reasoning lives at the `--rule` line in its workflow, which is the only place it can't go
stale.
"""

import re
import tempfile
import unittest
from pathlib import Path

from meta.scripts.classify_changed_paths import (
    classify,
    format_outputs,
    is_branch_creation,
    parse_rule,
)

# Not .resolve(): the workflows are cross-package data deps, so they live in the runfiles tree
# beside this file rather than at the source path a resolved symlink would lead back to.
_WORKFLOWS = Path(__file__).parent.parent.parent / ".github" / "workflows"


def rules_from_workflow(path: Path) -> dict[str, str]:
    """Extract the `--rule 'name=regex'` arguments a workflow passes to the classifier."""
    specs = re.findall(r"--rule\s+'([^']*)'", path.read_text(encoding="utf-8"))
    if not specs:
        raise AssertionError(f"no --rule arguments found in {path}")
    # Split here rather than via parse_rule: a fixture that leans on the function under test
    # would go quiet in exactly the case parse_rule's own tests are meant to catch.
    return {spec.partition("=")[0]: spec.partition("=")[2] for spec in specs}


RULES_RENOVATE = rules_from_workflow(_WORKFLOWS / "renovate-derived-files.yml")
RULES_DEVCONTAINER = rules_from_workflow(_WORKFLOWS / "devcontainer.yml")


def expect_devcontainer(**fired):
    """Full RULES_DEVCONTAINER classification: every group False except the ones named."""
    result = dict.fromkeys(RULES_DEVCONTAINER, False)
    result.update(fired)
    return result


def expect(**fired):
    """Full RULES_RENOVATE classification: every group False except the ones named."""
    result = dict.fromkeys(RULES_RENOVATE, False)
    result.update(fired)
    return result


class TestRuleExtraction(unittest.TestCase):
    """Guards the fixtures themselves: a silently empty extraction would make everything below
    assert against nothing."""

    def test_devcontainer_rule_names(self):
        self.assertEqual(sorted(RULES_DEVCONTAINER), ["base", "changed"])

    def test_renovate_rule_names(self):
        self.assertEqual(sorted(RULES_RENOVATE), ["bazel", "devcontainer", "go", "python"])

    def test_a_workflow_without_rules_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "workflow.yml"
            empty.write_text("jobs: {}\n", encoding="utf-8")
            with self.assertRaises(AssertionError):
                rules_from_workflow(empty)

    def test_regexes_survive_extraction_intact(self):
        # The `--rule` arguments are single-quoted in the workflow, so nothing in a regex needs
        # unescaping — pin that, since a quoting change would land here as a subtly wrong rule.
        self.assertEqual(RULES_DEVCONTAINER["base"], r"^meta/devcontainer-base/|^MODULE\.bazel$")


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
        self.assertEqual(self._run(["MODULE.bazel"]), expect(bazel=True))

    def test_nested_module_bazel(self):
        # `$`-anchored, not whole-line: a nested MODULE.bazel still counts.
        self.assertEqual(self._run(["sub/mod/MODULE.bazel"]), expect(bazel=True))

    def test_bazelversion_triggers_bazel_refresh(self):
        # A bazel version bump changes the lock's `lockFileVersion`, so it needs the same
        # `bazel mod deps` refresh as a MODULE.bazel change — same output, one gate. The
        # full-dict compare also pins that such a PR skips uv setup, the Go tidy, and the
        # devcontainer refresh entirely.
        self.assertEqual(self._run([".bazelversion"]), expect(bazel=True))

    def test_decoy_bazelversion_suffix(self):
        # `$`-anchored: a sibling like .bazelversion.bak must not trigger a refresh.
        self.assertEqual(self._run([".bazelversion.bak"]), expect())

    def test_module_bazel_lock_is_not_a_manifest(self):
        # The derived lock must NOT trigger a regen (nothing to regenerate from it).
        self.assertEqual(self._run(["MODULE.bazel.lock"]), expect())

    def test_requirements_lock_is_python_not_bazel(self):
        # Classification is independent: whether a Python change refreshes the Bazel lock
        # is decided later, on whether requirements_lock.txt actually moved.
        self.assertEqual(self._run(["requirements_lock.txt"]), expect(python=True))

    def test_nested_pyproject(self):
        self.assertEqual(self._run(["apps/foo/pyproject.toml"]), expect(python=True))

    def test_nested_go_mod(self):
        # A Renovate Go bump edits a per-module go.mod; the `(^|/)` prefix catches it at depth.
        self.assertEqual(self._run(["tools/net/go.mod"]), expect(go=True))

    def test_go_work(self):
        # go.work carries the go directive + workspace member list.
        self.assertEqual(self._run(["go.work"]), expect(go=True))

    def test_go_sum_is_derived_not_a_trigger(self):
        # go.sum is regenerated by tidy; it must not be the sole reason the workflow fires
        # (a Renovate bump always moves go.mod alongside it, which does trigger).
        self.assertEqual(self._run(["tools/net/go.sum"]), expect())

    def test_go_work_sum_is_derived_not_a_trigger(self):
        # `go\.work$` is end-anchored, so go.work.sum (the derived workspace sum) does not match.
        self.assertEqual(self._run(["go.work.sum"]), expect())

    def test_combined(self):
        self.assertEqual(self._run(["MODULE.bazel", "uv.lock"]), expect(python=True, bazel=True))

    def test_combined_go_python_bazel(self):
        # A grouped Renovate PR can touch all three ecosystems at once; every flag fires and
        # all derived files ride the workflow's single commit.
        self.assertEqual(
            self._run(["MODULE.bazel", "uv.lock", "tools/net/go.mod"]),
            expect(python=True, bazel=True, go=True),
        )

    def test_unrelated_only(self):
        self.assertEqual(self._run(["README.md"]), expect())

    def test_empty(self):
        self.assertEqual(self._run([]), expect())

    def test_decoy_module_bazel_template(self):
        self.assertEqual(self._run(["infra/MODULE.bazel.tmpl"]), expect())

    def test_decoy_uv_lock_backup(self):
        self.assertEqual(self._run(["uv.lock.bak"]), expect())

    def test_decoy_go_mod_backup(self):
        # Trailing suffix after go.mod must not match the `$`-anchored rule.
        self.assertEqual(self._run(["tools/net/go.mod.bak"]), expect())

    def test_devcontainer_json(self):
        # A Renovate feature bump edits devcontainer.json; the lock is keyed by the reference
        # string, so every entry restales and `devcontainer upgrade` has to re-resolve it.
        self.assertEqual(self._run([".devcontainer/devcontainer.json"]), expect(devcontainer=True))

    def test_devcontainer_lock_is_derived_not_a_trigger(self):
        # Same posture as MODULE.bazel.lock: the derived file must not trigger its own regen.
        self.assertEqual(self._run([".devcontainer/devcontainer-lock.json"]), expect())

    def test_other_devcontainer_files_are_not_a_trigger(self):
        # The Dockerfile and the lifecycle scripts feed the image build, not the feature lock;
        # devcontainer.yml covers those. Only devcontainer.json moves the references.
        self.assertEqual(self._run([".devcontainer/Dockerfile"]), expect())
        self.assertEqual(self._run([".devcontainer/post-create.sh"]), expect())

    def test_nested_devcontainer_json_is_not_matched(self):
        # `^`-anchored, unlike the Go/Python rules: this repo has exactly one devcontainer, and
        # a vendored copy under some subdirectory is not ours to re-resolve.
        self.assertEqual(self._run(["vendor/.devcontainer/devcontainer.json"]), expect())

    def test_combined_with_devcontainer(self):
        # A grouped Renovate PR can move a feature alongside Go/Python/Bazel; all four flags
        # fire and every derived file rides the single commit.
        self.assertEqual(
            self._run(
                ["MODULE.bazel", "uv.lock", "tools/net/go.mod", ".devcontainer/devcontainer.json"]
            ),
            expect(python=True, bazel=True, go=True, devcontainer=True),
        )


class TestClassifyDevcontainer(unittest.TestCase):
    def _run(self, files):
        return classify(files, RULES_DEVCONTAINER)

    def test_devcontainer_dir(self):
        # Builds the devcontainer but not the base image: nothing shared changed.
        self.assertEqual(self._run([".devcontainer/Dockerfile"]), expect_devcontainer(changed=True))

    def test_workflow_file(self):
        self.assertEqual(
            self._run([".github/workflows/devcontainer.yml"]), expect_devcontainer(changed=True)
        )

    def test_other_workflow_not_matched(self):
        # The `$` on the second alternative keeps a sibling workflow from matching.
        self.assertEqual(self._run([".github/workflows/ci.yml"]), expect_devcontainer())

    def test_unrelated(self):
        self.assertEqual(self._run(["README.md"]), expect_devcontainer())

    def test_base_image_fires_both(self):
        # A base change fires both. `changed` additionally gates the login and image-ref steps
        # the base build depends on, plus the consumer build this repo's FROM makes the real
        # validation of a base change. The job itself has no `if` and always runs, so this is
        # about the steps inside it, not about the job skipping.
        self.assertEqual(
            self._run(["meta/devcontainer-base/scripts/lib.sh"]),
            expect_devcontainer(changed=True, base=True),
        )

    def test_module_bazel_fires_both(self):
        # MODULE.bazel pins the base image's own base (the devcontainers_base_debian oci.pull),
        # and that bump automerges. Missing it here would move the pin without rebuilding,
        # smoke-testing or republishing anything — the bump would reach no image at all.
        self.assertEqual(self._run(["MODULE.bazel"]), expect_devcontainer(changed=True, base=True))

    def test_module_bazel_lock_is_derived_not_a_trigger(self):
        # The lock records no oci extension state (every pull is digest-pinned, hence
        # reproducible), so it never carries a base change of its own.
        self.assertEqual(self._run(["MODULE.bazel.lock"]), expect_devcontainer())

    def test_nested_module_bazel_is_not_matched(self):
        # `^`-anchored: only the root module declares the image's base.
        self.assertEqual(self._run(["vendor/MODULE.bazel"]), expect_devcontainer())

    def test_base_is_a_subset_of_changed(self):
        # The BASE_IMAGE override only reaches the build when both fire — the load step is
        # gated on `base` and the consumer build on `changed`. A path that set `base` alone
        # would load an image nothing then builds against.
        for path in (
            "meta/devcontainer-base/scripts/lib.sh",
            "meta/devcontainer-base/BUILD.bazel",
            "MODULE.bazel",
        ):
            with self.subTest(path=path):
                result = self._run([path])
                self.assertTrue(result["base"])
                self.assertTrue(result["changed"])

    def test_base_image_readme_also_counts(self):
        # The README is the canonical rationale home; cheap to rebuild, and keeping the rule
        # a plain prefix avoids a per-file allowlist drifting from the directory.
        self.assertEqual(
            self._run(["meta/devcontainer-base/README.md"]),
            expect_devcontainer(changed=True, base=True),
        )

    def test_sibling_meta_dir_not_matched(self):
        self.assertEqual(self._run(["meta/scripts/check_modules.py"]), expect_devcontainer())


class TestFormatOutputs(unittest.TestCase):
    def test_render(self):
        self.assertEqual(
            format_outputs({"python": True, "bazel": False}), "python=true\nbazel=false\n"
        )

    def test_empty(self):
        self.assertEqual(format_outputs({}), "")


if __name__ == "__main__":
    unittest.main()
