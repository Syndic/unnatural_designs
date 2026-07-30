"""Tests for this repo's devcontainer host stub (`initialize.sh`).

The stub's job is to *present* host state in the shape the shared plumbing consumes — it does
not constrain where the host keeps anything. Its discovery and sanitising steps are factored
into pure `initialize_*` functions so they can be exercised here without touching host state:
each test sources the script, whose imperative body is guarded on `BASH_SOURCE == $0`.

The shared library these values are handed to is tested in meta/devcontainer-base/.
"""

import subprocess
import sys
import unittest
from pathlib import Path

_INITIALIZE_SH = Path(__file__).resolve().parent / "initialize.sh"

_SOURCE_AND_CALL = 'set -euo pipefail; source "$1"; shift; "$@"'
_SOURCE_ONLY = 'set -euo pipefail; source "$1"'


def _sh(func: str, *args: str) -> str:
    """Call one shell function and return its stdout, minus the trailing newline."""
    result = subprocess.run(
        ["bash", "-c", _SOURCE_AND_CALL, "_", str(_INITIALIZE_SH), func, *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"{func}{args} failed (rc={result.returncode})\n"
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )
    return result.stdout.rstrip("\n")


class TestSourcingIsInert(unittest.TestCase):
    """The guard that makes every other test in this file safe."""

    def test_sourcing_produces_no_output_and_no_error(self):
        # If the imperative body ran it would write .git-plumbing/ and say so.
        result = subprocess.run(
            ["bash", "-c", _SOURCE_ONLY, "_", str(_INITIALIZE_SH)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")


class TestKnownHostsDiscovery(unittest.TestCase):
    """Picks the file ssh itself writes, so a host accepted in a container reaches the host."""

    def _first(self, value: str) -> str:
        return _sh("initialize_first_abs_path", value)

    def test_first_of_several_wins(self):
        # ssh appends newly-accepted hosts to the first UserKnownHostsFile only.
        self.assertEqual(
            self._first("/home/u/.ssh/known_hosts /home/u/.ssh/known_hosts2"),
            "/home/u/.ssh/known_hosts",
        )

    def test_single_value_passes_through(self):
        self.assertEqual(self._first("/custom/kh"), "/custom/kh")

    def test_leading_whitespace_tolerated(self):
        self.assertEqual(self._first("   /home/u/.ssh/known_hosts"), "/home/u/.ssh/known_hosts")

    def test_empty_yields_empty_so_the_caller_can_default(self):
        # Lets the caller fall back to ssh's default rather than linking to nothing.
        self.assertEqual(self._first(""), "")

    def test_unexpanded_tokens_are_rejected(self):
        # An ssh that printed these literally would otherwise have the caller mkdir a literal
        # `~` in the workspace and then dangle the symlink, aborting container start.
        for literal in ("~/.ssh/known_hosts", "%d/.ssh/known_hosts", ".ssh/known_hosts"):
            with self.subTest(value=literal):
                self.assertEqual(self._first(literal), "")

    def test_absolute_path_with_traversal_is_kept(self):
        # Deliberately not rejected: unprivileged, on the user's own filesystem, and dropping
        # it would silently ignore a legitimate config. Contrast plumbing_git_path_is_safe,
        # which does reject it because it runs under sudo in the container.
        self.assertEqual(
            self._first("/home/u/../u/.ssh/known_hosts"), "/home/u/../u/.ssh/known_hosts"
        )


class TestTimezoneDiscovery(unittest.TestCase):
    """Host-side parsing whose output feeds a privileged `ln` in the container."""

    def test_zone_extracted_from_zoneinfo_link(self):
        self.assertEqual(
            _sh("initialize_zone_from_link", "/var/db/timezone/zoneinfo/America/Los_Angeles"),
            "America/Los_Angeles",
        )

    def test_non_zoneinfo_link_yields_empty(self):
        self.assertEqual(_sh("initialize_zone_from_link", "/etc/somewhere"), "")

    def test_plain_zone_passes_through(self):
        self.assertEqual(_sh("initialize_sanitize_tz", "Europe/Berlin"), "Europe/Berlin")

    def test_absolute_path_is_rejected(self):
        self.assertEqual(_sh("initialize_sanitize_tz", "/etc/shadow"), "")

    def test_traversal_is_rejected(self):
        for hostile in ("../../etc/shadow", "America/../../..", "a/..b/c"):
            with self.subTest(value=hostile):
                self.assertEqual(_sh("initialize_sanitize_tz", hostile), "")

    def test_empty_stays_empty(self):
        self.assertEqual(_sh("initialize_sanitize_tz", ""), "")


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
