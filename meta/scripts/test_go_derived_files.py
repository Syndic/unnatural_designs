"""Tests for go_derived_files.py.

The pure functions (module_dirs, derived_files) carry all the logic; tests focus there. The
go.work parse (registered_modules) and the `go mod tidy` / `go work sync` side effects are
exercised end-to-end by renovate-derived-files.yml on real PRs.
"""

import unittest
from pathlib import Path

from meta.scripts.go_derived_files import derived_files, module_dirs


class TestModuleDirs(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(module_dirs([]), [])

    def test_sorted(self):
        self.assertEqual(module_dirs([Path("tools/b"), Path("tools/a")]), ["tools/a", "tools/b"])

    def test_single(self):
        self.assertEqual(
            module_dirs([Path("tools/network_infrastructure_maintenance")]),
            ["tools/network_infrastructure_maintenance"],
        )


class TestDerivedFiles(unittest.TestCase):
    def test_empty_still_has_workspace_sum(self):
        # No members yet: `go work sync` can still touch go.work.sum, so it is always listed.
        self.assertEqual(derived_files([]), ["go.work.sum"])

    def test_single_module_gomod_before_gosum(self):
        self.assertEqual(
            derived_files([Path("tools/net")]),
            ["tools/net/go.mod", "tools/net/go.sum", "go.work.sum"],
        )

    def test_multiple_modules_sorted(self):
        self.assertEqual(
            derived_files([Path("tools/b"), Path("tools/a")]),
            [
                "tools/a/go.mod",
                "tools/a/go.sum",
                "tools/b/go.mod",
                "tools/b/go.sum",
                "go.work.sum",
            ],
        )

    def test_workspace_sum_is_last(self):
        # The commit list must end with the workspace-level sum regardless of member count.
        self.assertEqual(derived_files([Path("m")])[-1], "go.work.sum")


if __name__ == "__main__":
    unittest.main()
