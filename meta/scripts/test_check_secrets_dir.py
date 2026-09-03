"""Tests for check_secrets_dir.py."""

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from meta.scripts.check_secrets_dir import main, run, tracked_files


def count(files: list[str]) -> int:
    """How many of `files` are violations."""
    return run([Path(f) for f in files])


def status(files: list[str]) -> int:
    """The process exit status for an argv of `files`."""
    with patch("sys.argv", ["check_secrets_dir.py", *files]):
        return main()


class TestViolationCount(unittest.TestCase):
    def test_allowed_file_is_not_a_violation(self):
        self.assertEqual(count(["secrets/secrets.md"]), 0)

    def test_token_file_counted(self):
        self.assertEqual(count(["secrets/.netbox_api_token"]), 1)

    def test_arbitrary_file_counted(self):
        self.assertEqual(count(["secrets/some_password.txt"]), 1)

    def test_nested_file_counted(self):
        self.assertEqual(count(["secrets/subdir/file.txt"]), 1)

    def test_multiple_violations_all_counted(self):
        self.assertEqual(count(["secrets/foo.txt", "secrets/bar.txt"]), 2)

    def test_mixed_allowed_and_violation(self):
        self.assertEqual(count(["secrets/secrets.md", "secrets/oops.txt"]), 1)

    def test_file_named_secrets_md_outside_secrets_dir_counted(self):
        # The allowed path is exact — a file with the same name elsewhere must not pass.
        self.assertEqual(count(["docs/secrets.md"]), 1)

    def test_file_with_secrets_prefix_outside_dir_counted(self):
        self.assertEqual(count(["secrets_backup/secrets.md"]), 1)


class TestExitStatus(unittest.TestCase):
    """main() reports pass/fail, never the finding count -- see _workspace.exit_status."""

    def test_clean_run_exits_zero(self):
        self.assertEqual(status(["secrets/secrets.md"]), 0)

    def test_one_violation_exits_one(self):
        self.assertEqual(status(["secrets/leak.txt"]), 1)

    def test_several_violations_still_exit_one(self):
        self.assertEqual(status(["secrets/a.pem", "secrets/b.pem", "secrets/c.pem"]), 1)

    def test_exactly_256_violations_does_not_wrap_to_success(self):
        # Pins that main() routes through the boundary; the boundary itself is covered by
        # TestExitStatus in test__workspace.py.
        files = [f"secrets/f{i}.txt" for i in range(256)]
        self.assertEqual(count(files), 256)
        self.assertEqual(status(files), 1)


class TestTrackedTreeMode(unittest.TestCase):
    """No arguments -- the CI path, which enumerates the tree rather than trusting a caller."""

    def tree_status(self, tracked: list[str]) -> int:
        with patch(
            "meta.scripts.check_secrets_dir.tracked_files",
            return_value=[Path(p) for p in tracked],
        ):
            return status([])

    def test_placeholder_alone_passes(self):
        self.assertEqual(self.tree_status(["secrets/secrets.md"]), 0)

    def test_empty_tree_passes(self):
        self.assertEqual(self.tree_status([]), 0)

    def test_committed_file_alongside_placeholder_fails(self):
        self.assertEqual(self.tree_status(["secrets/secrets.md", "secrets/leak.txt"]), 1)

    def test_argv_is_not_what_makes_it_pass(self):
        # The bug this mode closes: with no argv the check inspected nothing and exited 0
        # whatever secrets/ actually held.
        self.assertEqual(self.tree_status(["secrets/leak.txt"]), 1)


class TestTrackedFiles(unittest.TestCase):
    """The git invocation itself. Pinned because *tracked* is the load-bearing word."""

    def setUp(self):
        root = patch("meta.scripts.check_secrets_dir.workspace_root", return_value=Path("/repo"))
        root.start()
        self.addCleanup(root.stop)

    def _ls_files(self, stdout: str):
        return patch(
            "meta.scripts.check_secrets_dir.subprocess.run",
            return_value=Mock(stdout=stdout),
        )

    def test_lists_tracked_paths_under_secrets(self):
        with self._ls_files("secrets/secrets.md\nsecrets/leak.txt\n") as sp:
            files = tracked_files()
        self.assertEqual(files, [Path("secrets/secrets.md"), Path("secrets/leak.txt")])
        self.assertEqual(sp.call_args.args[0], ["git", "ls-files", "--", "secrets"])
        self.assertEqual(sp.call_args.kwargs["cwd"], Path("/repo"))

    def test_asks_git_for_tracked_files_not_the_working_tree(self):
        # `git ls-files` without --others is what keeps a developer's own gitignored
        # secrets out of the result; a filesystem walk would report them as violations.
        with self._ls_files("") as sp:
            tracked_files()
        self.assertNotIn("--others", sp.call_args.args[0])

    def test_blank_output_yields_no_files(self):
        with self._ls_files("\n"):
            self.assertEqual(tracked_files(), [])


if __name__ == "__main__":
    unittest.main()
