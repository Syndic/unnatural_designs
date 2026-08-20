"""Guards the CodeQL job's Go toolchain step against the workflow it lives in.

CodeQL analyses Go by building it, and the autobuilder runs under GOTOOLCHAIN=local — so the
toolchain on PATH when extraction starts is the only one it can use. The `actions/setup-go` step
ahead of `codeql-action/init` is what puts go.work's version there. Drop that step, unhook it from
go.work, or let it drift after init, and nothing fails until go.work asks for a newer Go than the
runner image ships. That is the failure this job was written for: under GitHub's managed default
setup there was no step to add, and the first bump past the image's Go took Go analysis down with
`go.work requires go >= 1.27.0 (running go 1.26.6)`.

Scope is that coupling plus the matrix entries it hangs off. The language list is a policy choice
rather than a hand-copy of anything in the repo, so it is not asserted; the build modes are, since
a language that cannot use `none` is a language that needs a toolchain installed for it — and an
entry that names no mode at all is the same case wearing a default.

The `codeql-all` fan-in is here for the same reason. It is the name the ruleset requires, so it is
what makes every matrix row required — but only while it depends on the matrix and runs when the
matrix fails. Drop `if: always()` and the job is skipped rather than failed, which branch
protection reads as a pass: the gate is still listed, still green, and no longer gating.
"""

import re
import unittest
from pathlib import Path

# Not .resolve(): the workflow is a cross-package data dep, so it lives in the runfiles tree beside
# this file rather than at the source path a resolved symlink would lead back to.
_WORKFLOW = Path(__file__).parent.parent.parent / ".github" / "workflows" / "security.yml"

_SETUP_GO = "actions/setup-go@"
_CODEQL_INIT = "github/codeql-action/init@"


def job_block(text: str, job: str) -> str:
    """One job's lines: its key through the line before the next job key."""
    start = re.search(rf"^  {re.escape(job)}:$", text, re.M)
    if start is None:
        raise AssertionError(f"no `{job}:` job in {_WORKFLOW.name}")
    rest = text[start.end() :]
    end = re.search(r"^  [a-zA-Z_][\w-]*:$", rest, re.M)
    return rest[: end.start()] if end else rest


def step_block(block: str, uses: str) -> str:
    """One step's lines, found by the action it `uses`, through the line before the next step."""
    start = block.index(f"- uses: {uses}")
    rest = block[start:]
    end = re.search(r"^      - ", rest[1:], re.M)
    return rest[: end.start() + 1] if end else rest


_CODEQL = job_block(_WORKFLOW.read_text(encoding="utf-8"), "codeql")

# Split on the list marker and read each entry's keys separately, rather than matching a
# `- language:`/`build-mode:` pair. A pattern that wants both keys adjacent silently drops the entry
# that omits `build-mode` — and that is the dangerous one, since codeql-action then falls back to
# the language's own default, which is autobuild for every language that cannot use `none`.
_ENTRY_SPLIT_RE = re.compile(r"^ +- ", re.M)
_KEY_RE = re.compile(r"^ *([\w-]+): (\S+)$", re.M)
_LANGUAGE_KEY_RE = re.compile(r"^ *(?:- )?language:", re.M)


def matrix_include() -> str:
    """The job's matrix `include:` block, comments above it excluded."""
    return _CODEQL[_CODEQL.index("include:") : _CODEQL.index("    steps:")]


def matrix_entries() -> list[dict[str, str]]:
    """The `include:` entries, each as the keys it sets."""
    return [dict(_KEY_RE.findall(chunk)) for chunk in _ENTRY_SPLIT_RE.split(matrix_include())[1:]]


class MatrixTest(unittest.TestCase):
    def setUp(self):
        self.entries = matrix_entries()

    def test_every_entry_is_read(self):
        """Non-vacuity guard: an entry parsed away is an entry the assertions below never see."""
        self.assertEqual(
            len(self.entries),
            len(_LANGUAGE_KEY_RE.findall(matrix_include())),
            f"the `include:` entries in {_WORKFLOW.name} did not all parse",
        )
        self.assertTrue(self.entries, f"no `include:` entries in {_WORKFLOW.name}")

    def test_every_entry_declares_a_build_mode(self):
        """An omitted `build-mode:` is not a neutral default; it is autobuild where it matters."""
        self.assertEqual(
            [entry.get("language", entry) for entry in self.entries if "build-mode" not in entry],
            [],
            "codeql-action falls back to the language's own default build mode, which is "
            "autobuild for exactly the languages that cannot use `none` — say the mode here",
        )

    def test_go_is_the_only_language_that_builds(self):
        built = {
            entry.get("language"): entry.get("build-mode")
            for entry in self.entries
            if entry.get("build-mode") != "none"
        }
        self.assertEqual(
            built,
            {"go": "autobuild"},
            "a language analysed by building needs its toolchain installed before "
            "codeql-action/init, the way the setup-go step does for Go — wire one up (and widen "
            "that step's `if:`) before adding it here",
        )


class FanInTest(unittest.TestCase):
    """The job the ruleset names, and the properties that make requiring it mean something."""

    def setUp(self):
        self.fan_in = job_block(_WORKFLOW.read_text(encoding="utf-8"), "codeql-all")

    def test_fan_in_depends_on_the_matrix_job(self):
        self.assertIn(
            "needs: [codeql]",
            self.fan_in,
            "`codeql-all` is the required check; a matrix row it does not depend on is a row "
            "nothing gates",
        )

    def test_fan_in_runs_even_when_the_matrix_fails(self):
        self.assertIn(
            "if: always()",
            self.fan_in,
            "without `if: always()` a failed matrix skips `codeql-all`, and branch protection "
            "counts a skipped required check as passed",
        )

    def test_fan_in_accepts_only_success(self):
        """`skipped` must not pass here, unlike devcontainer.yml's gated `base-image-all`."""
        self.assertIn('!= "success"', self.fan_in)


class ToolchainStepTest(unittest.TestCase):
    def setUp(self):
        self.step = step_block(_CODEQL, _SETUP_GO)

    def test_toolchain_comes_from_go_work(self):
        self.assertIn("go-version-file: go.work", self.step)

    def test_step_is_conditioned_on_the_go_language(self):
        self.assertIn("if: matrix.language == 'go'", self.step)

    def test_step_precedes_codeql_init(self):
        self.assertLess(
            _CODEQL.index(_SETUP_GO),
            _CODEQL.index(_CODEQL_INIT),
            "setup-go runs after codeql-action/init, so extraction still gets the runner "
            "image's Go",
        )


if __name__ == "__main__":
    unittest.main()
