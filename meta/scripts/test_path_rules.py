"""Tests for the shared path-classification rule sets and the loader that flattens them.

Two things are covered here, and the split matters. `_path_rules.py` is the loader — composition,
dedupe, and the ways a rules file can be wrong — and it is exercised against synthetic fixtures.
The rule sets themselves are exercised against the *real* `.github/path-rules.toml`, because that
is the file two workflows and a pre-commit hook actually classify with; a synthetic copy would
assert nothing about them.

This coverage used to live in `test_classify_changed_paths.py`, reading `--rule` arguments back
out of the workflows. It moved when the rules did: that suite tests the classifier, and the rules
are no longer part of it.
"""

import re
import tempfile
import unittest
from pathlib import Path

from meta.scripts._path_rules import RuleError, load_rules, select

# Not .resolve(): the rules file is a cross-package data dep, so it lives in the runfiles tree
# beside this file rather than at the source path a resolved symlink would lead back to.
_RULES_FILE = Path(__file__).parent.parent.parent / ".github" / "path-rules.toml"

RULES = load_rules(_RULES_FILE)


def fires(path: str) -> set[str]:
    """Every rule set the given path matches. One namespace, so this is the whole picture."""
    return {name for name, pattern in RULES.items() if re.search(pattern, path)}


def write_rules(tmp: str, body: str) -> Path:
    path = Path(tmp) / "path-rules.toml"
    path.write_text(body, encoding="utf-8")
    return path


class TestRuleSetsPresent(unittest.TestCase):
    """Guards the fixture itself: a silently empty load would make everything below vacuous."""

    def test_the_expected_sets_exist(self):
        self.assertEqual(
            sorted(RULES), ["base", "bazel", "changed", "devcontainer", "go", "python"]
        )

    def test_every_set_has_a_usable_pattern(self):
        for name, pattern in RULES.items():
            with self.subTest(name=name):
                self.assertTrue(pattern)
                re.compile(pattern)


class TestComposition(unittest.TestCase):
    """The relations that used to be comments asking a reader to keep two regexes in step."""

    def test_base_is_declared_to_include_bazel(self):
        # Asserted on the declaration, not just on behaviour: the point of the change is that the
        # relation is structural. Sample paths could pass while the include was deleted and the
        # patterns copied back in.
        import tomllib

        sets = tomllib.loads(_RULES_FILE.read_text(encoding="utf-8"))["rules"]
        self.assertIn("bazel", sets["base"]["include"])
        self.assertIn("base", sets["changed"]["include"])

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


class TestLoader(unittest.TestCase):
    """The loader, against synthetic fixtures — every way a rules file can be wrong."""

    def test_composition_flattens_own_patterns_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_rules(
                tmp,
                "[rules.inner]\npatterns = ['^a$']\n\n"
                "[rules.outer]\ninclude = ['inner']\npatterns = ['^b$']\n",
            )
            self.assertEqual(load_rules(path), {"inner": "^a$", "outer": "^b$|^a$"})

    def test_composition_is_transitive(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_rules(
                tmp,
                "[rules.a]\npatterns = ['^a$']\n\n"
                "[rules.b]\ninclude = ['a']\npatterns = ['^b$']\n\n"
                "[rules.c]\ninclude = ['b']\npatterns = ['^c$']\n",
            )
            self.assertEqual(load_rules(path)["c"], "^c$|^b$|^a$")

    def test_a_shared_pattern_is_not_repeated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_rules(
                tmp,
                "[rules.a]\npatterns = ['^x$']\n\n"
                "[rules.b]\npatterns = ['^x$']\n\n"
                "[rules.c]\ninclude = ['a', 'b']\n",
            )
            self.assertEqual(load_rules(path)["c"], "^x$")

    def test_a_set_may_be_pure_composition(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_rules(tmp, "[rules.a]\npatterns = ['^x$']\n\n[rules.b]\ninclude = ['a']\n")
            self.assertEqual(load_rules(path)["b"], "^x$")

    def test_unknown_include_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_rules(tmp, "[rules.a]\ninclude = ['nope']\npatterns = ['^x$']\n")
            with self.assertRaises(RuleError):
                load_rules(path)

    def test_include_cycle_is_an_error(self):
        # Left unguarded this is a RecursionError, which reads as a crash rather than a bad file.
        with tempfile.TemporaryDirectory() as tmp:
            path = write_rules(tmp, "[rules.a]\ninclude = ['b']\n\n[rules.b]\ninclude = ['a']\n")
            with self.assertRaises(RuleError):
                load_rules(path)

    def test_an_empty_set_is_an_error(self):
        # A set that matches nothing would emit `name=false` forever, which is indistinguishable
        # from a rule that is simply never hit.
        with tempfile.TemporaryDirectory() as tmp:
            path = write_rules(tmp, "[rules.a]\npatterns = []\n")
            with self.assertRaises(RuleError):
                load_rules(path)

    def test_a_file_with_no_sets_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_rules(tmp, "# nothing here\n")
            with self.assertRaises(RuleError):
                load_rules(path)

    def test_an_uncompilable_pattern_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_rules(tmp, "[rules.a]\npatterns = ['(unclosed']\n")
            with self.assertRaises(RuleError):
                load_rules(path)


class TestSelect(unittest.TestCase):
    def test_returns_the_named_sets_in_the_order_asked_for(self):
        self.assertEqual(list(select({"a": "1", "b": "2", "c": "3"}, ["c", "a"])), ["c", "a"])

    def test_an_unknown_name_is_an_error(self):
        # Not an empty group: a caller naming a set that does not exist would otherwise emit
        # `name=false` on every run and gate its steps off forever.
        with self.assertRaises(RuleError):
            select({"a": "1"}, ["a", "nope"])


if __name__ == "__main__":
    unittest.main()
