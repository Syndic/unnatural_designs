"""Tests for check_go_work.py."""

import tempfile
import unittest
from pathlib import Path

from meta.scripts._workspace import found_modules, registered_modules


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
            self.assertEqual(registered_modules(root), set())

    def test_single_line_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_go_work(root, "go 1.26.1\nuse ./tools/foo\n")
            self.assertEqual(registered_modules(root), {Path("tools/foo")})

    def test_block_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_go_work(root, "go 1.26.1\nuse (\n    ./tools/foo\n    ./tools/bar\n)\n")
            self.assertEqual(
                registered_modules(root),
                {Path("tools/foo"), Path("tools/bar")},
            )

    def test_block_replace_no_false_positive(self):
        """Block-form replace directives with local paths must not be mistaken for use entries."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_go_work(root, "go 1.26.1\nreplace (\n    ./foo => ./bar\n)\n")
            self.assertEqual(registered_modules(root), set())

    def test_go_directive_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_go_work(root, "go 1.26.1\n")
            self.assertEqual(registered_modules(root), set())

    def test_toolchain_directive_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_go_work(root, "go 1.26.1\ntoolchain go1.26.1\n")
            self.assertEqual(registered_modules(root), set())


class TestFoundModules(unittest.TestCase):

    def test_no_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(found_modules(Path(tmp)), set())

    def test_finds_go_mod(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_go_mod(root, "tools/foo")
            self.assertEqual(found_modules(root), {Path("tools/foo")})

    def test_finds_multiple_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_go_mod(root, "tools/foo")
            write_go_mod(root, "libs/bar")
            self.assertEqual(found_modules(root), {Path("tools/foo"), Path("libs/bar")})

    def test_excludes_bazel_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_go_mod(root, "bazel-out/fake_module")
            self.assertEqual(found_modules(root), set())


class TestConsistency(unittest.TestCase):

    def test_consistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_go_work(root, "go 1.26.1\nuse ./tools/foo\n")
            write_go_mod(root, "tools/foo")
            registered = registered_modules(root)
            found = found_modules(root)
            self.assertEqual(found - registered, set())
            self.assertEqual(registered - found, set())

    def test_missing_use_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_go_work(root, "go 1.26.1\n")
            write_go_mod(root, "tools/foo")
            self.assertEqual(
                found_modules(root) - registered_modules(root),
                {Path("tools/foo")},
            )

    def test_stale_use_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_go_work(root, "go 1.26.1\nuse ./tools/nonexistent\n")
            self.assertEqual(
                registered_modules(root) - found_modules(root),
                {Path("tools/nonexistent")},
            )


if __name__ == "__main__":
    unittest.main()
