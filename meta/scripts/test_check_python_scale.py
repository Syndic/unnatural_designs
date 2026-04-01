"""Tests for check_python_scale.py."""

import tempfile
import unittest
from pathlib import Path

from meta.scripts.check_python_scale import check, count_python_targets


def write_build(root: Path, rel_path: str, content: str) -> None:
    build_file = root / rel_path / "BUILD.bazel"
    build_file.parent.mkdir(parents=True, exist_ok=True)
    build_file.write_text(content)


class TestCountPythonTargets(unittest.TestCase):

    def test_no_build_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(count_python_targets(Path(tmp)), {})

    def test_build_file_with_no_python_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_build(root, "tools/foo", 'go_binary(name = "foo")\n')
            self.assertEqual(count_python_targets(root), {})

    def test_counts_py_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_build(root, "libs/foo", 'py_library(name = "foo")\n')
            self.assertEqual(
                count_python_targets(root),
                {Path("libs/foo/BUILD.bazel"): 1},
            )

    def test_counts_py_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_build(root, "tools/foo", 'py_binary(name = "foo")\n')
            self.assertEqual(
                count_python_targets(root),
                {Path("tools/foo/BUILD.bazel"): 1},
            )

    def test_counts_py_test(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_build(root, "libs/foo", 'py_test(name = "test_foo")\n')
            self.assertEqual(
                count_python_targets(root),
                {Path("libs/foo/BUILD.bazel"): 1},
            )

    def test_counts_multiple_targets_in_one_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_build(root, "libs/foo", (
                'py_library(name = "foo")\n'
                'py_test(name = "test_foo")\n'
            ))
            self.assertEqual(
                count_python_targets(root),
                {Path("libs/foo/BUILD.bazel"): 2},
            )

    def test_counts_across_multiple_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_build(root, "libs/foo", 'py_library(name = "foo")\n')
            write_build(root, "libs/bar", 'py_library(name = "bar")\n')
            self.assertEqual(sum(count_python_targets(root).values()), 2)

    def test_ignores_commented_out_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_build(root, "libs/foo", (
                'py_library(name = "foo")\n'
                '# py_binary(name = "unused")\n'
            ))
            self.assertEqual(
                count_python_targets(root),
                {Path("libs/foo/BUILD.bazel"): 1},
            )

    def test_excludes_bazel_symlink_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_build(root, "bazel-out/libs/foo", 'py_library(name = "foo")\n')
            self.assertEqual(count_python_targets(root), {})


class TestCheck(unittest.TestCase):

    def test_passes_below_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_build(root, "libs/foo", 'py_library(name = "foo")\n')
            self.assertEqual(check(root, threshold=5), 0)

    def test_passes_at_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            content = "".join(
                f'py_library(name = "lib{i}")\n' for i in range(5)
            )
            write_build(root, "libs/foo", content)
            self.assertEqual(check(root, threshold=5), 0)

    def test_fails_above_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            content = "".join(
                f'py_library(name = "lib{i}")\n' for i in range(6)
            )
            write_build(root, "libs/foo", content)
            self.assertEqual(check(root, threshold=5), 1)


if __name__ == "__main__":
    unittest.main()
