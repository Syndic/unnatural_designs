"""Tests for _workspace.py.

Every helper is covered here and only here. A consumer's own suite tests that it is wired to a
helper — `main()` routing through `exit_status`, a walker skipping `bazel-*` — not the helper.
"""

import tempfile
import unittest
from pathlib import Path

from meta.scripts._workspace import (
    col_range,
    exit_status,
    find_files,
    find_go_modules,
    find_python_projects,
    is_skipped,
    registered_modules,
)


def write_go_mod(root: Path, rel_path: str) -> None:
    mod_dir = root / rel_path
    mod_dir.mkdir(parents=True, exist_ok=True)
    (mod_dir / "go.mod").write_text(f"module github.com/Syndic/unnatural_designs/{rel_path}\n")


def write_pyproject(root: Path, rel_path: str) -> None:
    proj_dir = root / rel_path
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "pyproject.toml").write_text(f'[project]\nname = "{rel_path.replace("/", "-")}"\n')


def write_go_work(root: Path, content: str) -> None:
    (root / "go.work").write_text(content)


class TestExitStatus(unittest.TestCase):
    """The boundary that keeps a finding count out of the process status."""

    def test_no_findings_is_success(self):
        self.assertEqual(exit_status(0), 0)

    def test_one_finding_is_failure(self):
        self.assertEqual(exit_status(1), 1)

    def test_the_truncating_count_is_still_failure(self):
        # sys.exit(256) leaves the shell with status 0, which is the case this exists for.
        self.assertEqual(exit_status(256), 1)

    def test_shell_reserved_counts_are_still_failure(self):
        # 126 not-executable, 127 not-found, 128+n signalled, 255 out-of-range: a count landing
        # on any of these would impersonate a different failure rather than report its own.
        for n in (126, 127, 128, 137, 255):
            with self.subTest(count=n):
                self.assertEqual(exit_status(n), 1)


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


class TestFindGoModules(unittest.TestCase):
    def test_no_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(find_go_modules(Path(tmp)), set())

    def test_finds_every_module_relative_to_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_go_mod(root, "tools/foo")
            write_go_mod(root, "libs/bar")
            self.assertEqual(find_go_modules(root), {Path("tools/foo"), Path("libs/bar")})

    def test_skipped_directories_hold_no_modules(self):
        for rel in ("bazel-out/fake", ".git/fake"):
            with self.subTest(path=rel), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                write_go_mod(root, rel)
                self.assertEqual(find_go_modules(root), set())


class TestFindPythonProjects(unittest.TestCase):
    def test_no_projects(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(find_python_projects(Path(tmp)), set())

    def test_excludes_root_pyproject(self):
        """The workspace root pyproject is not a project; discovery skips it."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname = 'root'\n")
            self.assertEqual(find_python_projects(root), set())

    def test_finds_subdir_projects(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_pyproject(root, "tools/foo")
            write_pyproject(root, "libs/bar")
            self.assertEqual(find_python_projects(root), {Path("tools/foo"), Path("libs/bar")})

    def test_skipped_directories_hold_no_projects(self):
        for rel in (".venv/some-dep", "bazel-out/fake"):
            with self.subTest(path=rel), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                write_pyproject(root, rel)
                self.assertEqual(find_python_projects(root), set())


class TestRegisteredModules(unittest.TestCase):
    def test_empty_go_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_go_work(root, "go 1.26.1\n")
            self.assertEqual(registered_modules(root), {})

    def test_single_line_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_go_work(root, "go 1.26.1\nuse ./tools/foo\n")
            # Line numbers are part of the contract — they drive squiggle placement.
            self.assertEqual(registered_modules(root), {Path("tools/foo"): 2})

    def test_block_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_go_work(root, "go 1.26.1\nuse (\n    ./tools/foo\n    ./tools/bar\n)\n")
            self.assertEqual(
                registered_modules(root),
                {Path("tools/foo"): 3, Path("tools/bar"): 4},
            )

    def test_block_replace_no_false_positive(self):
        """Block-form replace directives with local paths must not be mistaken for use entries."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_go_work(root, "go 1.26.1\nreplace (\n    ./foo => ./bar\n)\n")
            self.assertEqual(registered_modules(root), {})

    def test_go_directive_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_go_work(root, "go 1.26.1\n")
            self.assertEqual(registered_modules(root), {})

    def test_toolchain_directive_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_go_work(root, "go 1.26.1\ntoolchain go1.26.1\n")
            self.assertEqual(registered_modules(root), {})


if __name__ == "__main__":
    unittest.main()
