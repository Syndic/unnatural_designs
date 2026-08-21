#!/usr/bin/env python3
"""Unit tests for check_adr_numbers.py."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from meta.scripts.check_adr_numbers import adr_files, violations


def _paths(*names: str) -> list[Path]:
    return [Path(n) for n in names]


class TestAdrFiles(unittest.TestCase):
    """Discovery has to span every docs/adr/ in the tree, and nothing else."""

    def _tree(self, *rel: str) -> Path:
        root = Path(tempfile.mkdtemp())
        for r in rel:
            p = root / r
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# x\n")
        return root

    def test_finds_root_and_context_directories(self):
        root = self._tree("docs/adr/0001-a.md", "meta/docs/adr/0002-b.md")
        self.assertEqual(adr_files(root), _paths("docs/adr/0001-a.md", "meta/docs/adr/0002-b.md"))

    def test_ignores_markdown_outside_an_adr_directory(self):
        # A check that swept every *.md would flag the repo's ordinary docs.
        root = self._tree("docs/adr/0001-a.md", "docs/agents/domain.md", "README.md")
        self.assertEqual(adr_files(root), _paths("docs/adr/0001-a.md"))

    def test_ignores_an_adr_directory_not_under_docs(self):
        root = self._tree("adr/0001-a.md", "docs/adr/0002-b.md")
        self.assertEqual(adr_files(root), _paths("docs/adr/0002-b.md"))

    def test_skips_build_output_and_vcs_directories(self):
        # find_files' skip list is what keeps a bazel-* symlink from doubling every ADR.
        root = self._tree("docs/adr/0001-a.md", "bazel-out/docs/adr/0001-a.md")
        self.assertEqual(adr_files(root), _paths("docs/adr/0001-a.md"))


class TestViolations(unittest.TestCase):
    def test_unique_numbers_pass(self):
        self.assertEqual(violations(_paths("docs/adr/0001-a.md", "meta/docs/adr/0002-b.md")), [])

    def test_duplicate_across_directories_fails(self):
        # The case global numbering exists to prevent: per-directory numbering produces this.
        found = violations(_paths("docs/adr/0001-a.md", "meta/docs/adr/0001-b.md"))
        self.assertEqual(len(found), 1)
        path, message = found[0]
        self.assertEqual(path, Path("meta/docs/adr/0001-b.md"))
        self.assertIn("duplicate ADR number 0001", message)
        self.assertIn("docs/adr/0001-a.md", message)

    def test_duplicate_within_one_directory_fails(self):
        found = violations(_paths("docs/adr/0001-a.md", "docs/adr/0001-b.md"))
        self.assertEqual(len(found), 1)

    def test_a_third_use_is_reported_too(self):
        # Reporting only the first collision would let a fix land and still leave a duplicate.
        found = violations(_paths("docs/adr/0001-a.md", "docs/adr/0001-b.md", "docs/adr/0001-c.md"))
        self.assertEqual(len(found), 2)

    def test_readme_is_exempt(self):
        self.assertEqual(violations(_paths("docs/adr/README.md", "docs/adr/0001-a.md")), [])

    def test_malformed_names_are_reported(self):
        for name in (
            "1-a.md",
            "00001-a.md",
            "0001a.md",
            "0001-.md",
            "0001-Mixed-Case.md",
            "notes.md",
        ):
            with self.subTest(name=name):
                found = violations(_paths(f"docs/adr/{name}"))
                self.assertEqual(len(found), 1, f"{name} should be rejected")
                self.assertIn("not a valid ADR filename", found[0][1])

    def test_malformed_name_is_not_also_counted_as_a_number(self):
        # A file that fails the shape has no number to collide with; reporting both would
        # double-count one mistake.
        found = violations(_paths("docs/adr/0001-a.md", "docs/adr/0001a.md"))
        self.assertEqual(len(found), 1)
        self.assertIn("not a valid ADR filename", found[0][1])

    def test_valid_slugs_with_digits_are_accepted(self):
        self.assertEqual(violations(_paths("docs/adr/0007-use-oauth2-for-sso.md")), [])


if __name__ == "__main__":
    unittest.main()
