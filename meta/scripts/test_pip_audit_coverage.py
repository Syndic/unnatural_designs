"""Holds pip-audit's export to the flags that decide what it actually scans.

`uv export` emits the **root** project's dependencies unless asked for the whole workspace. So
without `--all-packages`, a workspace member that is registered in `[tool.uv.workspace].members`,
resolved into `uv.lock`, and passing every check in ci.yml still contributes nothing to the file
pip-audit reads — the job goes green having looked at none of its dependencies.

Reproduced on uv 0.12.5 with a two-package workspace whose member depends on `idna`: the default
export carries `packaging` (the root's dep) and no `idna`; adding `--all-packages` carries both.

Nothing fails when the flag is dropped. `members` is `[]` today, so the export is correct by
coincidence rather than by construction, and the day a member lands is the day the coincidence
ends — silently, since a passing scan of a smaller set looks exactly like a passing scan.

`--no-emit-workspace` is the second half. It supersedes `--no-emit-project` (uv: "Do not emit any
workspace members, including the root project"), keeping first-party packages out of the audit as
`--all-packages` pulls them in. Swapping it back for `--no-emit-project` would emit members into a
file consumed with `--require-hashes`.
"""

import re
import unittest
from pathlib import Path

# Not .resolve(): the workflow is a cross-package data dep, so it lives in the runfiles tree
# beside this file rather than at the source path a resolved symlink would lead back to.
_WORKFLOW = Path(__file__).parent.parent.parent / ".github" / "workflows" / "security.yml"

_REQUIRED_FLAGS = ("--all-packages", "--no-emit-workspace")


def export_command() -> str:
    """The `uv export` invocation from the pip-audit job, line continuations folded out."""
    text = _WORKFLOW.read_text(encoding="utf-8")
    match = re.search(r"^\s*(uv export\b[\s\S]*?)(?=\n\s*- name:|\n\s*-\s+uses:)", text, re.M)
    if match is None:
        raise AssertionError("no `uv export` invocation in security.yml")
    return " ".join(match.group(1).replace("\\\n", " ").split())


class ExportCoverageTest(unittest.TestCase):
    def setUp(self):
        self.command = export_command()

    def test_export_covers_every_workspace_member(self):
        self.assertIn(
            "--all-packages",
            self.command,
            "without it `uv export` emits only the root project's dependencies, so a workspace "
            "member's deps are never handed to pip-audit and the job passes having skipped them",
        )

    def test_first_party_packages_stay_out_of_the_audit(self):
        self.assertIn(
            "--no-emit-workspace",
            self.command,
            "`--all-packages` pulls members into the export; this is what keeps them from being "
            "emitted into a file that pip-audit consumes with --require-hashes",
        )

    def test_the_superseded_flag_is_not_left_behind(self):
        """`--no-emit-project` drops only the root, so alongside `--all-packages` it is a bug."""
        self.assertNotIn("--no-emit-project", self.command)

    def test_the_command_was_actually_read(self):
        """Non-vacuity guard: a regex that matched nothing would satisfy the negative test above."""
        self.assertTrue(self.command.startswith("uv export "), self.command)
        self.assertIn("--output-file", self.command, "the export's own contract, not this test's")


class WorkflowPinTest(unittest.TestCase):
    """The comment at the step is the only place a reader learns why the flags are there."""

    def test_the_step_explains_the_flags(self):
        text = _WORKFLOW.read_text(encoding="utf-8")
        for flag in _REQUIRED_FLAGS:
            with self.subTest(flag=flag):
                # Named in prose somewhere other than the command line itself.
                self.assertGreater(
                    text.count(flag),
                    1,
                    f"{flag} appears only in the command; a reader deleting it finds no reason "
                    "not to",
                )


if __name__ == "__main__":
    unittest.main()
