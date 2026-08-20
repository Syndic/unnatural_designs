"""Guards the CodeQL job's Go toolchain step against the workflow it lives in.

CodeQL analyses Go by building it, and the autobuilder runs under GOTOOLCHAIN=local — so the
toolchain on PATH when extraction starts is the only one it can use. The `actions/setup-go` step
ahead of `codeql-action/init` is what puts go.work's version there. Drop that step, unhook it from
go.work, or let it drift after init, and nothing fails until go.work asks for a newer Go than the
runner image ships. That is the failure this job was written for: under GitHub's managed default
setup there was no step to add, and the first bump past the image's Go took Go analysis down with
`go.work requires go >= 1.27.0 (running go 1.26.6)`.

Scope is that coupling plus the matrix entry it hangs off. The language list is a policy choice
rather than a hand-copy of anything in the repo, so it is not asserted; the build modes are, since
a language that cannot use `none` is a language that needs a toolchain installed for it.
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

# Positional pairing, not a lookup per key: it is what lets the build-mode assertion below name the
# language it is complaining about.
_MATRIX_RE = re.compile(r"^\s+- language: (\S+)\n\s+build-mode: (\S+)$", re.M)


def build_modes() -> dict[str, str]:
    """The job's matrix, as {language: build-mode}."""
    return dict(_MATRIX_RE.findall(_CODEQL))


class MatrixTest(unittest.TestCase):
    def test_matrix_is_read(self):
        """Non-vacuity guard: an empty parse would make every assertion below pass silently."""
        self.assertTrue(build_modes(), f"no `- language:`/`build-mode:` pairs in {_WORKFLOW.name}")

    def test_go_is_the_only_language_that_builds(self):
        built = {lang: mode for lang, mode in build_modes().items() if mode != "none"}
        self.assertEqual(
            built,
            {"go": "autobuild"},
            "a language analysed by building needs its toolchain installed before "
            "codeql-action/init, the way the setup-go step does for Go — wire one up (and widen "
            "that step's `if:`) before adding it here",
        )


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
