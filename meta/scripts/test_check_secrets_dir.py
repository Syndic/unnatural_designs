"""Tests for check_secrets_dir.py."""

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from meta.scripts.check_secrets_dir import main, tracked_files


def run(files: list[str]) -> int:
    with patch("sys.argv", ["check_secrets_dir.py", *files]):
        return main()


class TestFilenameMode(unittest.TestCase):
    """Filenames passed explicitly -- the pre-commit path."""

    def test_allowed_file_passes(self):
        self.assertEqual(run(["secrets/secrets.md"]), 0)

    def test_token_file_rejected(self):
        self.assertEqual(run(["secrets/.netbox_api_token"]), 1)

    def test_arbitrary_file_rejected(self):
        self.assertEqual(run(["secrets/some_password.txt"]), 1)

    def test_nested_file_rejected(self):
        self.assertEqual(run(["secrets/subdir/file.txt"]), 1)

    def test_multiple_violations_all_counted(self):
        self.assertEqual(run(["secrets/foo.txt", "secrets/bar.txt"]), 2)

    def test_mixed_allowed_and_violation(self):
        self.assertEqual(run(["secrets/secrets.md", "secrets/oops.txt"]), 1)

    def test_file_named_secrets_md_outside_secrets_dir_rejected(self):
        # The allowed path is exact — a file with the same name elsewhere must not pass.
        self.assertEqual(run(["docs/secrets.md"]), 1)

    def test_file_with_secrets_prefix_outside_dir_rejected(self):
        self.assertEqual(run(["secrets_backup/secrets.md"]), 1)


class TestTrackedTreeMode(unittest.TestCase):
    """No arguments -- the CI path, which enumerates the tree rather than trusting a caller."""

    def check(self, tracked: list[str]) -> int:
        with patch(
            "meta.scripts.check_secrets_dir.tracked_files",
            return_value=[Path(p) for p in tracked],
        ):
            return run([])

    def test_placeholder_alone_passes(self):
        self.assertEqual(self.check(["secrets/secrets.md"]), 0)

    def test_empty_tree_passes(self):
        self.assertEqual(self.check([]), 0)

    def test_committed_file_alongside_placeholder_rejected(self):
        self.assertEqual(self.check(["secrets/secrets.md", "secrets/leak.txt"]), 1)

    def test_multiple_violations_all_counted(self):
        self.assertEqual(self.check(["secrets/a.pem", "secrets/b.pem"]), 2)

    def test_argv_is_not_what_makes_it_pass(self):
        # The bug this mode exists to close: with no argv the check inspected nothing and
        # exited 0 whatever secrets/ actually held.
        self.assertEqual(self.check(["secrets/leak.txt"]), 1)


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
