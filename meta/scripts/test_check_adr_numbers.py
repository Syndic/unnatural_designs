#!/usr/bin/env python3
"""Unit tests for check_adr_numbers.py."""

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from meta.scripts.check_adr_numbers import adr_files, next_number, run, violations


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

    def test_duplicate_message_names_a_free_number(self):
        # The failure should carry its own fix rather than sending the reader back to --next.
        found = violations(_paths("docs/adr/0001-a.md", "docs/adr/0001-b.md"))
        self.assertIn("renumber to 0002 or later", found[0][1])

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


class TestNextNumber(unittest.TestCase):
    def test_empty_repo_starts_at_one(self):
        self.assertEqual(next_number([]), "0001")

    def test_is_max_plus_one_not_count_plus_one(self):
        # A gap from a deleted ADR must not be handed out again: 0002 may still be cited.
        self.assertEqual(next_number(_paths("docs/adr/0001-a.md", "docs/adr/0003-c.md")), "0004")

    def test_spans_every_directory(self):
        self.assertEqual(
            next_number(_paths("docs/adr/0001-a.md", "meta/docs/adr/0002-b.md")), "0003"
        )

    def test_ignores_malformed_and_exempt_names(self):
        self.assertEqual(
            next_number(_paths("docs/adr/0001-a.md", "docs/adr/README.md", "docs/adr/9999x.md")),
            "0002",
        )

    def test_answers_during_a_collision(self):
        # --next is how you get out of a duplicate, so it has to work while one exists.
        self.assertEqual(next_number(_paths("docs/adr/0001-a.md", "docs/adr/0001-b.md")), "0002")

    def test_pads_to_four_digits(self):
        self.assertEqual(next_number(_paths("docs/adr/0009-i.md")), "0010")


class TestRun(unittest.TestCase):
    """The CLI's two modes, driven without shelling out."""

    def _tree(self, *rel: str) -> Path:
        root = Path(tempfile.mkdtemp())
        for r in rel:
            p = root / r
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# x\n")
        return root

    def _capture(self, root: Path, next_only: bool) -> tuple[int, str]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = run(root, next_only)
        return code, buf.getvalue()

    def test_next_prints_only_the_number(self):
        # Printed bare so it can be substituted straight into a filename.
        root = self._tree("docs/adr/0001-a.md", "meta/docs/adr/0002-b.md")
        code, out = self._capture(root, next_only=True)
        self.assertEqual(code, 0)
        self.assertEqual(out, "0003\n")

    def test_next_succeeds_even_when_the_tree_is_invalid(self):
        root = self._tree("docs/adr/0001-a.md", "docs/adr/0001-b.md")
        code, out = self._capture(root, next_only=True)
        self.assertEqual(code, 0)
        self.assertEqual(out, "0002\n")

    def test_check_mode_reports_and_exits_nonzero(self):
        root = self._tree("docs/adr/0001-a.md", "meta/docs/adr/0001-b.md")
        code, out = self._capture(root, next_only=False)
        self.assertEqual(code, 1)
        self.assertIn("meta/docs/adr/0001-b.md:1:1-2:", out)

    def test_check_mode_is_quiet_and_zero_when_clean(self):
        root = self._tree("docs/adr/0001-a.md", "meta/docs/adr/0002-b.md")
        code, out = self._capture(root, next_only=False)
        self.assertEqual(code, 0)
        self.assertIn("unique across 2", out)


if __name__ == "__main__":
    unittest.main()
