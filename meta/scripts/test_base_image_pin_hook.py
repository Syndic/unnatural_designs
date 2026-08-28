"""Tests for base_image_pin_hook.py.

The hook's whole job is deciding whether it has work to do, so that decision is what is covered:
the pure match against the shared set, and the early return that keeps an unrelated commit from
paying for a Bazel invocation. The work itself is `sync_base_image_pin.py`, tested separately.
"""

import unittest
from pathlib import Path
from unittest import mock

from meta.scripts._path_rules import load_rules
from meta.scripts.base_image_pin_hook import _RULE, main, matching

# Not .resolve(): the rules file is a cross-package data dep living in the runfiles tree.
_RULES_FILE = Path(__file__).parent.parent.parent / ".github" / "path-rules.toml"
_BASE = load_rules(_RULES_FILE)[_RULE]


class TestMatching(unittest.TestCase):
    def test_picks_out_base_image_inputs(self):
        self.assertEqual(
            matching(["README.md", "MODULE.bazel", "meta/scripts/x.py"], _BASE), ["MODULE.bazel"]
        )

    def test_preserves_the_order_given(self):
        self.assertEqual(
            matching([".bazelversion", "MODULE.bazel"], _BASE), [".bazelversion", "MODULE.bazel"]
        )

    def test_no_inputs_is_empty(self):
        self.assertEqual(matching(["README.md", "docs/x.md"], _BASE), [])

    def test_no_files_at_all_is_empty(self):
        # pre-commit can invoke a hook with an empty list; that is not a reason to build.
        self.assertEqual(matching([], _BASE), [])


class TestMain(unittest.TestCase):
    """The gate, with the expensive half stubbed out."""

    def test_an_unrelated_commit_runs_no_bazel_and_succeeds(self):
        with (
            mock.patch("meta.scripts.base_image_pin_hook.subprocess.run") as run,
            mock.patch("meta.scripts.base_image_pin_hook.sync_pin") as sync,
        ):
            rc = main(["README.md", "--rules-file", str(_RULES_FILE)])
        self.assertEqual(rc, 0)
        run.assert_not_called()
        sync.assert_not_called()

    def test_a_base_input_builds_then_syncs(self):
        with (
            mock.patch("meta.scripts.base_image_pin_hook.subprocess.run") as run,
            mock.patch("meta.scripts.base_image_pin_hook.sync_pin", return_value=0) as sync,
        ):
            rc = main(["MODULE.bazel", "--rules-file", str(_RULES_FILE)])
        self.assertEqual(rc, 0)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0][:2], ["bazel", "build"])
        sync.assert_called_once()

    def test_the_sync_s_exit_code_is_the_hook_s(self):
        # A failed sync has to block the commit; swallowing it would let a stale pin through.
        with (
            mock.patch("meta.scripts.base_image_pin_hook.subprocess.run"),
            mock.patch("meta.scripts.base_image_pin_hook.sync_pin", return_value=2),
        ):
            self.assertEqual(main(["MODULE.bazel", "--rules-file", str(_RULES_FILE)]), 2)

    def test_one_matching_path_among_many_is_enough(self):
        with (
            mock.patch("meta.scripts.base_image_pin_hook.subprocess.run"),
            mock.patch("meta.scripts.base_image_pin_hook.sync_pin", return_value=0) as sync,
        ):
            rc = main(["README.md", ".bazelversion", "docs/x.md", "--rules-file", str(_RULES_FILE)])
        self.assertEqual(rc, 0)
        sync.assert_called_once()


if __name__ == "__main__":
    unittest.main()
