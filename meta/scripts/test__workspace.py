"""Tests for _workspace.py.

Covers the helpers introduced by the extraction: `is_skipped` and `find_files`.
The `found_modules` and `registered_modules` helpers retain their existing coverage in
test_check_go_work.py (now importing from _workspace).
"""

import tempfile
import unittest
from pathlib import Path

from meta.scripts._workspace import find_files, is_skipped


class TestIsSkipped(unittest.TestCase):

    def test_plain_path_not_skipped(self):
        self.assertFalse(is_skipped(Path("foo/bar.py")))

    def test_git_dir_skipped(self):
        self.assertTrue(is_skipped(Path(".git/HEAD")))

    def test_node_modules_skipped(self):
        self.assertTrue(is_skipped(Path("node_modules/some/file.js")))

    def test_bazel_prefix_skipped(self):
        self.assertTrue(is_skipped(Path("bazel-out/foo")))
        self.assertTrue(is_skipped(Path("bazel-bin/foo")))

    def test_skipped_component_anywhere_in_path(self):
        self.assertTrue(is_skipped(Path("foo/.git/bar")))
        self.assertTrue(is_skipped(Path("a/b/node_modules/c")))

    def test_substring_match_does_not_skip(self):
        # `not_bazel-` doesn't *start* with `bazel-`, and `git` is not exactly `.git`.
        self.assertFalse(is_skipped(Path("not_bazel-out/foo")))
        self.assertFalse(is_skipped(Path("git/HEAD")))


class TestFindFiles(unittest.TestCase):

    def test_no_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(find_files(Path(tmp), "*.go"), [])

    def test_single_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pkg").mkdir()
            (root / "pkg" / "foo.go").write_text("package foo\n")
            self.assertEqual(find_files(root, "*.go"), [root / "pkg" / "foo.go"])

    def test_excludes_bazel_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bazel-out").mkdir()
            (root / "bazel-out" / "foo.go").write_text("package foo\n")
            self.assertEqual(find_files(root, "*.go"), [])

    def test_excludes_git_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / ".git" / "foo.go").write_text("package foo\n")
            self.assertEqual(find_files(root, "*.go"), [])

    def test_excludes_node_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "node_modules").mkdir()
            (root / "node_modules" / "foo.go").write_text("package foo\n")
            self.assertEqual(find_files(root, "*.go"), [])

    def test_pattern_matches_exact_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a").mkdir()
            (root / "a" / "go.mod").write_text("module a\n")
            (root / "a" / "go.sum").write_text("")
            self.assertEqual(find_files(root, "go.mod"), [root / "a" / "go.mod"])


if __name__ == "__main__":
    unittest.main()
