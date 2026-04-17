"""Tests for check_secrets_dir.py."""

import unittest
from unittest.mock import patch

from meta.scripts.check_secrets_dir import main


def run(files: list[str]) -> int:
    with patch("sys.argv", ["check_secrets_dir.py"] + files):
        return main()


class TestCheckSecretsDir(unittest.TestCase):

    def test_allowed_file_passes(self):
        self.assertEqual(run(["secrets/secrets.md"]), 0)

    def test_no_files_passes(self):
        self.assertEqual(run([]), 0)

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


if __name__ == "__main__":
    unittest.main()
