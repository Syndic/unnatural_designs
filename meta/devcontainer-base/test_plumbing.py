"""Tests for the shared devcontainer host-plumbing library.

The plumbing stays shell because the base image it ships in
(`mcr.microsoft.com/devcontainers/base:debian`) has no Python interpreter, and adding one
would cost the RUN-free property that keeps multi-arch builds free. Its decision logic is
therefore factored into side-effect-free functions so it can be exercised from here: each
test sources `lib.sh` and calls one function, so nothing privileged ever runs.

Host-stub functions (`initialize_*`) are tested separately, in .devcontainer/, next to the
script that owns them.
"""

import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_LIB_SH = _HERE / "scripts" / "lib.sh"
_DISPATCHER = _HERE / "scripts" / "devcontainer-plumbing.sh"

# Source the library, drop it from the argv, then invoke the remaining args as a command.
# lib.sh is a pure library — sourcing it runs nothing.
_SOURCE_AND_CALL = 'set -euo pipefail; source "$1"; shift; "$@"'
_SOURCE_ONLY = 'set -euo pipefail; source "$1"'


@contextmanager
def _tmpdir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def _run(script: Path, func: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", _SOURCE_AND_CALL, "_", str(script), func, *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _sh(script: Path, func: str, *args: str) -> str:
    """Call one shell function and return its stdout, minus the trailing newline."""
    result = _run(script, func, *args)
    if result.returncode != 0:
        raise AssertionError(
            f"{func}{args} failed (rc={result.returncode})\n"
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )
    return result.stdout.rstrip("\n")


def _rc(script: Path, func: str, *args: str) -> int:
    """Return just the exit status, for the predicates that signal via rc."""
    return _run(script, func, *args).returncode


class TestSourcingIsInert(unittest.TestCase):
    """The guard that makes every other test in this file safe."""

    def test_sourcing_produces_no_output_and_no_error(self):
        result = subprocess.run(
            ["bash", "-c", _SOURCE_ONLY, "_", str(_LIB_SH)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")


class TestGitCommonAction(unittest.TestCase):
    """The guard that stops the runtime symlink clobbering a real .git directory."""

    def _action(self, path: str, workspace: str) -> str:
        return _sh(_LIB_SH, "plumbing_git_common_action", path, workspace)

    def test_path_inside_workspace_is_skipped(self):
        # Full-clone case: the bind mount already provides .git, so bridging is a no-op.
        self.assertEqual(self._action("/ws/repo/.git", "/ws/repo"), "skip")

    def test_sibling_sharing_a_prefix_is_not_treated_as_inside(self):
        # /ws/repo-other must not satisfy an "inside /ws/repo" test.
        with _tmpdir() as d:
            self.assertEqual(self._action(f"{d}/repo-other/.git", f"{d}/repo"), "create")

    def test_absent_path_is_created(self):
        # The linked-worktree case this whole mechanism exists for.
        with _tmpdir() as d:
            self.assertEqual(self._action(f"{d}/elsewhere/.git", f"{d}/ws"), "create")

    def test_existing_symlink_is_refreshed(self):
        with _tmpdir() as d:
            link = Path(d) / "link"
            link.symlink_to("/host-git-common")
            self.assertEqual(self._action(str(link), f"{d}/ws"), "refresh")

    def test_real_directory_is_a_conflict_not_a_clobber(self):
        # The bug this guard exists for: `ln -sfn` onto a real dir nests a stray link
        # inside it instead of replacing it.
        with _tmpdir() as d:
            real = Path(d) / "real.git"
            real.mkdir()
            self.assertEqual(self._action(str(real), f"{d}/ws"), "conflict")

    def test_real_file_is_a_conflict(self):
        with _tmpdir() as d:
            path = Path(d) / "afile"
            path.write_text("")
            self.assertEqual(self._action(str(path), f"{d}/ws"), "conflict")


class TestTzEnvironment(unittest.TestCase):
    """The rewrite that keeps /etc/environment idempotent across container starts."""

    def _render(self, tz: str, current: str) -> list:
        with _tmpdir() as d:
            envfile = Path(d) / "environment"
            envfile.write_text(current)
            return _sh(_LIB_SH, "plumbing_tz_environment", tz, str(envfile)).splitlines()

    def test_appends_when_absent(self):
        self.assertEqual(self._render("UTC", "PATH=/usr/bin\n"), ["PATH=/usr/bin", "TZ=UTC"])

    def test_applying_twice_does_not_duplicate(self):
        # postCreate and postStart both run this on the first `up`.
        once = self._render("America/Denver", "PATH=/usr/bin\nTZ=UTC\n")
        self.assertEqual(once, ["PATH=/usr/bin", "TZ=America/Denver"])
        self.assertEqual(self._render("America/Denver", "\n".join(once) + "\n"), once)

    def test_preserves_other_container_env_lines(self):
        current = "GOTOOLCHAIN=local\nTZ=UTC\nSSH_AUTH_SOCK=/run/x.sock\n"
        self.assertEqual(
            self._render("Europe/Paris", current),
            ["GOTOOLCHAIN=local", "SSH_AUTH_SOCK=/run/x.sock", "TZ=Europe/Paris"],
        )

    def test_indented_tz_line_is_also_replaced(self):
        self.assertEqual(self._render("UTC", "  TZ=stale\n"), ["TZ=UTC"])

    def test_missing_file_is_not_an_error(self):
        with _tmpdir() as d:
            out = _sh(_LIB_SH, "plumbing_tz_environment", "UTC", f"{d}/nonexistent")
        self.assertEqual(out.splitlines(), ["TZ=UTC"])

    def test_unreadable_file_fails_instead_of_truncating(self):
        # The caller writes this output over the real /etc/environment. A read failure must
        # not look like "the file only needed a TZ line" — that would wipe it. A directory
        # makes grep exit 2 regardless of uid, unlike chmod 000 which root ignores.
        with _tmpdir() as d:
            result = _run(_LIB_SH, "plumbing_tz_environment", "UTC", d)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("TZ=UTC", result.stdout)


class TestInputValidation(unittest.TestCase):
    """The library re-validates what the host stub wrote. The stub lives in the consuming
    repo, not here, and both values feed privileged commands."""

    def test_safe_zone_accepted(self):
        for good in ("UTC", "America/Los_Angeles", "Etc/GMT+5"):
            with self.subTest(value=good):
                self.assertEqual(_rc(_LIB_SH, "plumbing_zone_is_safe", good), 0)

    def test_unsafe_zone_rejected(self):
        for bad in ("", "/etc/shadow", "../../etc/shadow", "America/../.."):
            with self.subTest(value=bad):
                self.assertNotEqual(_rc(_LIB_SH, "plumbing_zone_is_safe", bad), 0)

    def test_absolute_git_path_accepted(self):
        self.assertEqual(_rc(_LIB_SH, "plumbing_git_path_is_safe", "/Users/x/repo/.git"), 0)

    def test_relative_or_traversing_git_path_rejected(self):
        for bad in ("", "repo/.git", "/Users/../etc", "/a/../../b"):
            with self.subTest(value=bad):
                self.assertNotEqual(_rc(_LIB_SH, "plumbing_git_path_is_safe", bad), 0)


class TestDispatcher(unittest.TestCase):
    """Argument handling and library resolution — the surface every consumer calls."""

    def _run_dispatcher(self, *args: str, env: dict | None = None):
        return subprocess.run(
            ["bash", str(_DISPATCHER), *args],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    def test_no_args_is_a_usage_error(self):
        result = self._run_dispatcher()
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage:", result.stderr)

    def test_too_many_args_is_a_usage_error(self):
        self.assertEqual(self._run_dispatcher("post-create", "extra").returncode, 2)

    def test_unknown_verb_is_a_usage_error(self):
        # A typo must not silently do nothing, nor half-apply under some other name.
        result = self._run_dispatcher("poststart")
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage:", result.stderr)

    def test_both_verbs_are_accepted(self):
        # They get past argument parsing; they fail later here because this test environment
        # has no /run to stamp and no sudo, which is exactly what rc != 2 distinguishes.
        for verb in ("post-create", "post-start"):
            with self.subTest(verb=verb):
                self.assertNotEqual(self._run_dispatcher(verb).returncode, 2)

    def test_library_is_found_beside_the_script(self):
        # The in-repo layout. The image layout uses the absolute fallback path, which can't be
        # exercised from here without installing into /usr/local.
        result = self._run_dispatcher("post-create")
        self.assertNotIn("cannot locate lib.sh", result.stderr)


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
