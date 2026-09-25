"""Every `--emit` a workflow or composite action passes to classify_changed_paths.py names a set.

`select()` refuses a name it does not know, so a renamed set fails the calling step rather than
emitting `false` forever — but it fails on a PR, after the rename is written, and in the one
workflow whose Renovate-only trigger means an ordinary PR never runs it. This holds every caller to
`SETS` at once, found by glob, so a new caller is covered with no edit here.

What a caller then *does* with a set's output is its own workflow's business, and stays in that
workflow's guard: `//meta/scripts:test_devcontainer_required_checks` and
`:test_commit_file_via_app_selftest`.
"""

import unittest
from pathlib import Path

from meta.scripts._workflows import action_yaml_files, run_scripts
from meta.scripts.classify_changed_paths import emitted_sets
from meta.scripts.path_classification_pattern_sets import SETS

# Not .resolve(): the workflows and actions are cross-package data deps, so they live in the
# runfiles tree beside this file rather than at the source path a resolved symlink leads back to.
_ROOT = Path(__file__).parent.parent.parent
_CLASSIFIER = "classify_changed_paths.py"


def callers() -> list[tuple[str, list[str]]]:
    """`(file:line, emitted set names)` for every step that runs the classifier."""
    return [
        (f"{path.relative_to(_ROOT)}:{line}", emitted_sets(script))
        for path in action_yaml_files(_ROOT)
        for line, script in run_scripts(path)
        if _CLASSIFIER in script
    ]


class ClassifierCallersTest(unittest.TestCase):
    def setUp(self):
        self.callers = callers()

    def test_callers_were_found(self):
        """Without the data deps, or after a rename, every assertion below passes vacuously."""
        self.assertTrue(self.callers, f"no step in the runfiles tree runs {_CLASSIFIER}")

    def test_every_caller_asks_for_at_least_one_set(self):
        for where, names in self.callers:
            with self.subTest(caller=where):
                self.assertTrue(
                    names, f"{where} runs {_CLASSIFIER} with no `--emit`, which argparse refuses"
                )

    def test_every_emitted_set_exists(self):
        for where, names in self.callers:
            with self.subTest(caller=where):
                self.assertEqual(
                    sorted(name for name in names if name not in SETS),
                    [],
                    f"{where} names a set path_classification_pattern_sets.py does not define, so "
                    "the classifier exits non-zero and the step fails",
                )


if __name__ == "__main__":
    unittest.main()
