"""Tests for the devcontainer host-plumbing shell helpers.

The scripts stay shell because the Phase 2 base image they move into
(`mcr.microsoft.com/devcontainers/base:debian`) ships no Python. Their decision logic is
factored into side-effect-free functions so it can be exercised from here: each test
sources the script and calls one function, so nothing privileged ever runs.
"""

import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PLUMBING_SH = _HERE / "plumbing.sh"
_INITIALIZE_SH = _HERE / "initialize.sh"

# Source the script, drop it from the argv, then invoke the remaining args as a command.
# Sourcing is safe because both scripts guard their imperative body on BASH_SOURCE == $0.
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
        for script in (_PLUMBING_SH, _INITIALIZE_SH):
            with self.subTest(script=script.name):
                result = subprocess.run(
                    ["bash", "-c", _SOURCE_ONLY, "_", str(script)],
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
        return _sh(_PLUMBING_SH, "plumbing_git_common_action", path, workspace)

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
            return _sh(_PLUMBING_SH, "plumbing_tz_environment", tz, str(envfile)).splitlines()

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
            out = _sh(_PLUMBING_SH, "plumbing_tz_environment", "UTC", f"{d}/nonexistent")
        self.assertEqual(out.splitlines(), ["TZ=UTC"])

    def test_unreadable_file_fails_instead_of_truncating(self):
        # The caller writes this output over the real /etc/environment. A read failure must
        # not look like "the file only needed a TZ line" — that would wipe it. A directory
        # makes grep exit 2 regardless of uid, unlike chmod 000 which root ignores.
        with _tmpdir() as d:
            result = _run(_PLUMBING_SH, "plumbing_tz_environment", "UTC", d)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("TZ=UTC", result.stdout)


class TestInputValidation(unittest.TestCase):
    """plumbing.sh re-validates what the host stub wrote: after Phase 2 that stub lives in
    another repo, and both values feed privileged commands."""

    def test_safe_zone_accepted(self):
        for good in ("UTC", "America/Los_Angeles", "Etc/GMT+5"):
            with self.subTest(value=good):
                self.assertEqual(_rc(_PLUMBING_SH, "plumbing_zone_is_safe", good), 0)

    def test_unsafe_zone_rejected(self):
        for bad in ("", "/etc/shadow", "../../etc/shadow", "America/../.."):
            with self.subTest(value=bad):
                self.assertNotEqual(_rc(_PLUMBING_SH, "plumbing_zone_is_safe", bad), 0)

    def test_absolute_git_path_accepted(self):
        self.assertEqual(_rc(_PLUMBING_SH, "plumbing_git_path_is_safe", "/Users/x/repo/.git"), 0)

    def test_relative_or_traversing_git_path_rejected(self):
        for bad in ("", "repo/.git", "/Users/../etc", "/a/../../b"):
            with self.subTest(value=bad):
                self.assertNotEqual(_rc(_PLUMBING_SH, "plumbing_git_path_is_safe", bad), 0)


class TestSignersAssertion(unittest.TestCase):
    """The most user-visible new failure path: a mismatch aborts `devcontainer up`."""

    _BOUND = "/home/u/.ssh/allowed_signers"

    def test_unset_is_ok(self):
        self.assertEqual(_rc(_INITIALIZE_SH, "initialize_signers_ok", "", self._BOUND), 0)

    def test_matching_path_is_ok(self):
        self.assertEqual(_rc(_INITIALIZE_SH, "initialize_signers_ok", self._BOUND, self._BOUND), 0)

    def test_different_path_is_rejected(self):
        # e.g. the common ~/.config/git/allowed_signers layout.
        self.assertNotEqual(
            _rc(
                _INITIALIZE_SH,
                "initialize_signers_ok",
                "/home/u/.config/git/allowed_signers",
                self._BOUND,
            ),
            0,
        )


class TestTimezoneDiscovery(unittest.TestCase):
    """Host-side parsing whose output feeds a privileged `ln` in the container."""

    def test_zone_extracted_from_zoneinfo_link(self):
        self.assertEqual(
            _sh(
                _INITIALIZE_SH,
                "initialize_zone_from_link",
                "/var/db/timezone/zoneinfo/America/Los_Angeles",
            ),
            "America/Los_Angeles",
        )

    def test_non_zoneinfo_link_yields_empty(self):
        self.assertEqual(_sh(_INITIALIZE_SH, "initialize_zone_from_link", "/etc/somewhere"), "")

    def test_plain_zone_passes_through(self):
        self.assertEqual(
            _sh(_INITIALIZE_SH, "initialize_sanitize_tz", "Europe/Berlin"), "Europe/Berlin"
        )

    def test_absolute_path_is_rejected(self):
        self.assertEqual(_sh(_INITIALIZE_SH, "initialize_sanitize_tz", "/etc/shadow"), "")

    def test_traversal_is_rejected(self):
        for hostile in ("../../etc/shadow", "America/../../..", "a/..b/c"):
            with self.subTest(value=hostile):
                self.assertEqual(_sh(_INITIALIZE_SH, "initialize_sanitize_tz", hostile), "")

    def test_empty_stays_empty(self):
        self.assertEqual(_sh(_INITIALIZE_SH, "initialize_sanitize_tz", ""), "")


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
