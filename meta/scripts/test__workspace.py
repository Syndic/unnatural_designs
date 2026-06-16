"""Tests for _workspace.py.

Covers the helpers introduced by the extraction: `is_skipped` and `find_files`.
The `found_modules` and `registered_modules` helpers retain their existing coverage in
test_check_go_work.py (now importing from _workspace).
"""

import tempfile
import unittest
from pathlib import Path

from meta.scripts._workspace import col_range, find_files, is_skipped


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

    def test_venv_skipped(self):
        self.assertTrue(is_skipped(Path(".venv/lib/site-packages/x.py")))
        self.assertTrue(is_skipped(Path("venv/bin/python")))

    def test_git_plumbing_skipped(self):
        # .git-plumbing holds host-state snapshots regenerated every devcontainer up — never
        # repo-managed source, so walkers should always skip it.
        self.assertTrue(is_skipped(Path(".git-plumbing/host-gitconfig")))

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


class TestColRange(unittest.TestCase):
    def _write(self, content: str) -> Path:
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as tmp:
            tmp.write(content)
        return Path(tmp.name)

    def test_finds_needle_at_start(self):
        f = self._write("hello\n")
        # 1-based; endCol exclusive, so 1-6 highlights "hello" (5 chars: cols 1..5).
        self.assertEqual(col_range(f, 1, "hello"), (1, 6))

    def test_finds_needle_with_leading_whitespace(self):
        f = self._write("          - tools/foo\n")
        # `tools/foo` starts at col 13 (after 10 spaces + "- "); len 9 → endCol 22.
        self.assertEqual(col_range(f, 1, "tools/foo"), (13, 22))

    def test_finds_needle_on_later_line(self):
        f = self._write("go 1.26.1\nuse ./tools/foo\n")
        # On line 2, `./tools/foo` starts at col 5 (after "use "); len 11 → endCol 16.
        self.assertEqual(col_range(f, 2, "./tools/foo"), (5, 16))

    def test_missing_needle_falls_back(self):
        f = self._write("hello\n")
        # No crash, no misplaced squiggle — fallback to 1-2 (one-char highlight at col 1).
        self.assertEqual(col_range(f, 1, "nope"), (1, 2))

    def test_missing_line_falls_back(self):
        f = self._write("hello\n")
        self.assertEqual(col_range(f, 99, "hello"), (1, 2))

    def test_missing_file_falls_back(self):
        # File doesn't exist — fallback applies (the path was wrong, not a crash condition).
        self.assertEqual(col_range(Path("/nonexistent/file"), 1, "x"), (1, 2))


if __name__ == "__main__":
    unittest.main()
