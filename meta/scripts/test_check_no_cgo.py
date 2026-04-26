"""Tests for check_no_cgo.py (source-level scan only — the transitive dep check needs a
real Go module + the `go` binary, and is exercised end-to-end in CI)."""

import tempfile
import unittest
from pathlib import Path

from meta.scripts.check_no_cgo import find_cgo_in_sources


def write(root: Path, rel: str, content: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


class TestFindCgoInSources(unittest.TestCase):

    def test_no_go_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(find_cgo_in_sources(Path(tmp)), [])

    def test_go_file_without_cgo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "pkg/foo.go", 'package foo\n\nimport "fmt"\n')
            self.assertEqual(find_cgo_in_sources(root), [])

    def test_detects_import_c(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "pkg/foo.go", (
                'package foo\n'
                '\n'
                '/*\n'
                '#include <stdio.h>\n'
                '*/\n'
                'import "C"\n'
            ))
            self.assertEqual(find_cgo_in_sources(root), [Path("pkg/foo.go")])

    def test_detects_import_c_with_other_imports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "pkg/foo.go", (
                'package foo\n'
                '\n'
                'import (\n'
                '    "fmt"\n'
                ')\n'
                '\n'
                'import "C"\n'
            ))
            self.assertEqual(find_cgo_in_sources(root), [Path("pkg/foo.go")])

    def test_does_not_match_string_literal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "pkg/foo.go", (
                'package foo\n'
                '\n'
                'var s = `import "C"`\n'  # backtick string, not a top-level import
            ))
            self.assertEqual(find_cgo_in_sources(root), [])

    def test_does_not_match_quoted_in_doc_comment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "pkg/foo.go", (
                'package foo\n'
                '\n'
                '// We do not use `import "C"` here.\n'
                'import "fmt"\n'
            ))
            self.assertEqual(find_cgo_in_sources(root), [])

    def test_finds_across_multiple_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "a/foo.go", 'package a\nimport "C"\n')
            write(root, "b/bar.go", 'package b\nimport "C"\n')
            offenders = sorted(find_cgo_in_sources(root))
            self.assertEqual(offenders, [Path("a/foo.go"), Path("b/bar.go")])

    def test_excludes_bazel_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "bazel-out/pkg/foo.go", 'package foo\nimport "C"\n')
            self.assertEqual(find_cgo_in_sources(root), [])

    def test_excludes_git_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, ".git/hooks/foo.go", 'package foo\nimport "C"\n')
            self.assertEqual(find_cgo_in_sources(root), [])


if __name__ == "__main__":
    unittest.main()
