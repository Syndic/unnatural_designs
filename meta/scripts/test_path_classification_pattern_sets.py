"""Tests for the shared path-classification pattern sets.

These are the sets two workflows and the `base-image-pin` pre-commit hook classify against, so the
tests are about *behaviour* — which sets a given path falls into — rather than about any loading
machinery, of which there is now none: the sets are module-level constants composed by set union.

This coverage used to live in `test_classify_changed_paths.py`, reading `--rule` arguments back out
of the workflows. It moved when the sets did: that suite tests the classifier, and the sets are no
longer part of it.
"""

import re
import unittest

from meta.scripts.path_classification_pattern_sets import BASE, BAZEL, CHANGED, SETS


def fires(path: str) -> set[str]:
    """Every set the given path matches. One namespace, so this is the whole picture."""
    return {
        name
        for name, patterns in SETS.items()
        if any(re.search(pattern, path) for pattern in patterns)
    }


class TestSetsPresent(unittest.TestCase):
    """A set missing from SETS is unselectable, and one with no patterns is silently never hit."""

    def test_the_expected_sets_exist(self):
        self.assertEqual(sorted(SETS), ["base", "bazel", "changed", "devcontainer", "go", "python"])

    def test_every_set_has_usable_patterns(self):
        for name, patterns in SETS.items():
            with self.subTest(name=name):
                self.assertTrue(patterns)
                for pattern in patterns:
                    re.compile(pattern)


class TestComposition(unittest.TestCase):
    """The relations that used to be comments asking a reader to keep two regexes in step."""

    def test_each_set_literally_contains_the_one_it_composes(self):
        # Asserted on the constants, not just on behaviour: the point is that the relation is
        # structural. Sample paths could pass while the splat was deleted and the patterns copied
        # back in, which is the drift this replaced.
        self.assertTrue(set(BAZEL) < set(BASE))
        self.assertTrue(set(BASE) < set(CHANGED))

    def test_bazel_is_a_subset_of_base_which_is_a_subset_of_changed(self):
        # The invariant the publish job and the pin re-derivation both depend on: a path that
        # feeds the image must reach the job that publishes it.
        for path in _SAMPLE_PATHS:
            with self.subTest(path=path):
                hit = fires(path)
                if "bazel" in hit:
                    self.assertIn("base", hit)
                if "base" in hit:
                    self.assertIn("changed", hit)


class TestBazelAndBaseImageSets(unittest.TestCase):
    def test_module_bazel(self):
        self.assertEqual(fires("MODULE.bazel"), {"bazel", "base", "changed"})

    def test_bazelversion(self):
        self.assertEqual(fires(".bazelversion"), {"bazel", "base", "changed"})

    def test_nested_module_bazel_is_not_matched(self):
        # Root-anchored: there is one Bazel module, at the root. This is narrower than the rule
        # renovate-derived-files.yml used to carry on its own, which matched at any depth — an
        # unreachable breadth, since that workflow's own `paths:` trigger names root MODULE.bazel.
        self.assertEqual(fires("vendor/MODULE.bazel"), set())

    def test_decoy_bazelversion_suffix(self):
        self.assertEqual(fires(".bazelversion.bak"), set())

    def test_module_bazel_lock_is_derived_not_a_trigger(self):
        # The derived lock must not trigger its own regeneration, and records no oci extension
        # state, so it never carries a base change either.
        self.assertEqual(fires("MODULE.bazel.lock"), set())

    def test_decoy_module_bazel_template(self):
        self.assertEqual(fires("infra/MODULE.bazel.tmpl"), set())

    def test_base_image_sources(self):
        self.assertEqual(fires("meta/devcontainer-base/scripts/lib.sh"), {"base", "changed"})

    def test_base_image_readme_also_counts(self):
        # A plain prefix, so the rationale doc rebuilds the image. Cheap, and an allowlist would
        # drift from the directory.
        self.assertEqual(fires("meta/devcontainer-base/README.md"), {"base", "changed"})

    def test_sibling_meta_dir_not_matched(self):
        self.assertEqual(fires("meta/scripts/check_modules.py"), set())


class TestDevcontainerSets(unittest.TestCase):
    def test_devcontainer_json_fires_both_its_sets(self):
        # `devcontainer` re-resolves the feature lock; `changed` rebuilds the container.
        self.assertEqual(fires(".devcontainer/devcontainer.json"), {"devcontainer", "changed"})

    def test_devcontainer_lock_rebuilds_but_does_not_re_resolve(self):
        # Same posture as MODULE.bazel.lock for its own regeneration; it is still under
        # .devcontainer/, so the container itself is rebuilt.
        self.assertEqual(fires(".devcontainer/devcontainer-lock.json"), {"changed"})

    def test_dockerfile_and_lifecycle_scripts_rebuild_only(self):
        self.assertEqual(fires(".devcontainer/Dockerfile"), {"changed"})
        self.assertEqual(fires(".devcontainer/post-create.sh"), {"changed"})

    def test_nested_devcontainer_json_is_not_matched(self):
        # `^`-anchored: this repo has exactly one devcontainer, and a vendored copy under some
        # subdirectory is not ours to re-resolve.
        self.assertEqual(fires("vendor/.devcontainer/devcontainer.json"), set())

    def test_own_workflow_rebuilds(self):
        self.assertEqual(fires(".github/workflows/devcontainer.yml"), {"changed"})

    def test_the_module_that_defines_the_sets_rebuilds(self):
        # `changed` has to cover the file that defines it. A set edit that classifies nothing still
        # imports, so without this every gated step skips and the required check goes green having
        # built nothing — and nothing fails afterwards either.
        self.assertEqual(fires("meta/scripts/path_classification_pattern_sets.py"), {"changed"})

    def test_sibling_workflow_does_not(self):
        self.assertEqual(fires(".github/workflows/ci.yml"), set())


class TestPythonAndGoSets(unittest.TestCase):
    def test_requirements_lock_is_python_not_bazel(self):
        # Whether a Python change also refreshes the Bazel lock is decided later, on whether
        # requirements_lock.txt actually moved.
        self.assertEqual(fires("requirements_lock.txt"), {"python"})

    def test_nested_pyproject(self):
        self.assertEqual(fires("apps/foo/pyproject.toml"), {"python"})

    def test_uv_lock(self):
        self.assertEqual(fires("uv.lock"), {"python"})

    def test_decoy_uv_lock_backup(self):
        self.assertEqual(fires("uv.lock.bak"), set())

    def test_nested_go_mod(self):
        # A Renovate Go bump edits a per-module go.mod; `(^|/)` catches it at depth.
        self.assertEqual(fires("tools/net/go.mod"), {"go"})

    def test_go_work(self):
        self.assertEqual(fires("go.work"), {"go"})

    def test_go_sum_is_derived_not_a_trigger(self):
        self.assertEqual(fires("tools/net/go.sum"), set())

    def test_go_work_sum_is_derived_not_a_trigger(self):
        self.assertEqual(fires("go.work.sum"), set())

    def test_decoy_go_mod_backup(self):
        self.assertEqual(fires("tools/net/go.mod.bak"), set())


class TestUnrelated(unittest.TestCase):
    def test_readme(self):
        self.assertEqual(fires("README.md"), set())


_SAMPLE_PATHS = (
    "MODULE.bazel",
    ".bazelversion",
    "vendor/MODULE.bazel",
    "MODULE.bazel.lock",
    "meta/devcontainer-base/scripts/lib.sh",
    "meta/devcontainer-base/README.md",
    "meta/scripts/check_modules.py",
    ".devcontainer/devcontainer.json",
    ".devcontainer/Dockerfile",
    ".github/workflows/devcontainer.yml",
    ".github/workflows/ci.yml",
    "requirements_lock.txt",
    "apps/foo/pyproject.toml",
    "tools/net/go.mod",
    "go.work",
    "README.md",
)


if __name__ == "__main__":
    unittest.main()
