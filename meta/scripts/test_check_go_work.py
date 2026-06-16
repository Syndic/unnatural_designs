"""Tests for check_go_work.py."""

import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from meta.scripts import check_go_work
from meta.scripts._workspace import find_go_modules, registered_modules


def write_go_work(root: Path, content: str) -> None:
    (root / "go.work").write_text(content)


def write_go_mod(root: Path, module_path: str) -> None:
    mod_dir = root / module_path
    mod_dir.mkdir(parents=True, exist_ok=True)
    (mod_dir / "go.mod").write_text(
        f"module github.com/Syndic/unnatural_designs/{module_path}\ngo 1.26.1\n"
    )


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


class TestFindGoModules(unittest.TestCase):
    def test_no_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(find_go_modules(Path(tmp)), set())

    def test_finds_go_mod(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_go_mod(root, "tools/foo")
            self.assertEqual(find_go_modules(root), {Path("tools/foo")})

    def test_finds_multiple_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_go_mod(root, "tools/foo")
            write_go_mod(root, "libs/bar")
            self.assertEqual(find_go_modules(root), {Path("tools/foo"), Path("libs/bar")})

    def test_excludes_bazel_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_go_mod(root, "bazel-out/fake_module")
            self.assertEqual(find_go_modules(root), set())


class TestConsistency(unittest.TestCase):
    def test_consistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_go_work(root, "go 1.26.1\nuse ./tools/foo\n")
            write_go_mod(root, "tools/foo")
            registered = set(registered_modules(root))
            found = find_go_modules(root)
            self.assertEqual(found - registered, set())
            self.assertEqual(registered - found, set())

    def test_missing_use_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_go_work(root, "go 1.26.1\n")
            write_go_mod(root, "tools/foo")
            self.assertEqual(
                find_go_modules(root) - set(registered_modules(root)),
                {Path("tools/foo")},
            )

    def test_stale_use_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_go_work(root, "go 1.26.1\nuse ./tools/nonexistent\n")
            self.assertEqual(
                set(registered_modules(root)) - find_go_modules(root),
                {Path("tools/nonexistent")},
            )


class TestMain(unittest.TestCase):
    def _run(self, *, found, registered):
        """registered may be a list of paths (each defaulting to line 1) or {path: line} dict."""
        if isinstance(registered, dict):
            reg_locs = {Path(p): n for p, n in registered.items()}
        else:
            reg_locs = {Path(p): 1 for p in registered}
        with (
            mock.patch.object(check_go_work, "workspace_root", return_value=Path("/fake")),
            mock.patch.object(
                check_go_work, "find_go_modules", return_value={Path(p) for p in found}
            ),
            mock.patch.object(check_go_work, "registered_modules", return_value=reg_locs),
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            rc = check_go_work.main()
        return rc, stdout.getvalue()

    def test_consistent(self):
        rc, out = self._run(found=["tools/foo", "tools/bar"], registered=["tools/foo", "tools/bar"])
        self.assertEqual(rc, 0)
        self.assertIn("consistent", out)
        self.assertNotIn("missing", out)
        self.assertNotIn("stale", out)

    def test_missing_entries_reported(self):
        rc, out = self._run(found=["tools/foo", "tools/bar"], registered=["tools/foo"])
        self.assertEqual(rc, 1)
        # Missing-entry diagnostics always emit the 1-2 anchor column range.
        self.assertIn("tools/bar/go.mod:1:1-2: missing from go.work: ./tools/bar", out)
        self.assertNotIn("stale entry", out)

    def test_stale_entries_reported(self):
        # /fake/go.work doesn't exist, so col_range falls back to 1-2. The end-to-end column
        # math is covered by TestColRange in test__workspace.py.
        rc, out = self._run(found=["tools/foo"], registered={"tools/foo": 3, "tools/bar": 7})
        self.assertEqual(rc, 1)
        self.assertIn("go.work:7:1-2: stale entry: use ./tools/bar", out)
        self.assertNotIn("missing from go.work", out)

    def test_both_missing_and_stale_reported(self):
        rc, out = self._run(
            found=["tools/foo", "tools/new"],
            registered={"tools/foo": 2, "tools/old": 5},
        )
        self.assertEqual(rc, 2)
        self.assertIn("tools/new/go.mod:1:1-2: missing from go.work: ./tools/new", out)
        self.assertIn("go.work:5:1-2: stale entry: use ./tools/old", out)

    def test_findings_sorted(self):
        rc, out = self._run(found=["tools/zebra", "tools/alpha"], registered=[])
        self.assertEqual(rc, 2)
        alpha = out.index("./tools/alpha")
        zebra = out.index("./tools/zebra")
        self.assertLess(alpha, zebra)


if __name__ == "__main__":
    unittest.main()
