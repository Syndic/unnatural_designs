"""Tests for the shared devcontainer host-plumbing library.

The plumbing stays shell because the base image it ships in
(`mcr.microsoft.com/devcontainers/base:debian`) has no Python interpreter, and adding one
would cost the RUN-free property that keeps multi-arch builds free. Its decision logic is
therefore factored into side-effect-free functions so it can be exercised from here: each
test sources `lib.sh` and calls one function, so nothing privileged ever runs.

Host-stub functions (`initialize_*`) are tested separately, in .devcontainer/, next to the
script that owns them.
"""

import os
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


class TestSharedIndexConfig(unittest.TestCase):
    """The two settings are a pair: half of them is the broken state, not a lesser one."""

    def test_pair_is_atomic_when_the_caller_suspends_errexit(self):
        # Bash suspends errexit for a function's whole body when it is invoked as the left
        # operand of `&&`/`||` or inside a condition. Two plain statements would then let the
        # first write fail, the second succeed, and the function still return 0 — leaving a
        # container with only core.trustctime, missing the setting whose absence produces the
        # "your local changes would be overwritten" breakage.
        #
        # A non-git directory won't do as the failure injector: both writes would fail, so the
        # chained and unchained forms return the same status. Only the FIRST call must fail.
        with _tmpdir() as d:
            fake_bin = Path(d) / "bin"
            fake_bin.mkdir()
            git = fake_bin / "git"
            git.write_text('#!/bin/sh\ncase "$*" in *core.checkstat*) exit 1 ;; esac\nexit 0\n')
            git.chmod(0o755)
            env = dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}")
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    'set -euo pipefail; source "$1"; '
                    'plumbing_apply_shared_index_config "$2" && echo UNEXPECTED_SUCCESS',
                    "_",
                    str(_LIB_SH),
                    d,
                ],
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )
        self.assertNotIn("UNEXPECTED_SUCCESS", result.stdout)
        self.assertNotEqual(result.returncode, 0)


class TestApplyAll(unittest.TestCase):
    """The step sequencer, including the policy fork that only Syndic/.dotfiles turns on —
    a branch nothing in this repo exercises by running the container."""

    def _apply(self, plumbing_dir: str, workspace: str, require: str | None = None):
        env = dict(os.environ)
        if require is not None:
            env["PLUMBING_REQUIRE_GIT_CHECKOUT"] = require
        return subprocess.run(
            [
                "bash",
                "-c",
                _SOURCE_AND_CALL,
                "_",
                str(_LIB_SH),
                "plumbing_apply_all",
                plumbing_dir,
                workspace,
            ],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    def test_absent_files_are_a_clean_no_op(self):
        # Nothing recorded: no git bridge, no config write, no timezone — and crucially no
        # privileged call, which is what makes this safe to run as a unit test.
        with _tmpdir() as d:
            result = self._apply(d, d)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")

    def test_empty_path_file_is_a_clean_no_op(self):
        # initialize.sh writes an empty file when the workspace isn't a git checkout.
        with _tmpdir() as d:
            (Path(d) / "host-git-common-path").write_text("")
            result = self._apply(d, d)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_require_git_checkout_turns_the_no_op_into_a_failure(self):
        # The .dotfiles policy: a non-git workspace is a bootstrap failure, not something to
        # shrug at. Returns before anything privileged.
        with _tmpdir() as d:
            result = self._apply(d, d, require="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("PLUMBING_REQUIRE_GIT_CHECKOUT", result.stderr)

    def test_require_flag_off_by_default_and_when_zero(self):
        for require in (None, "0"):
            with self.subTest(require=require), _tmpdir() as d:
                self.assertEqual(self._apply(d, d, require=require).returncode, 0)


class TestDispatcher(unittest.TestCase):
    """Argument handling and library resolution — the surface every consumer calls."""

    def _run_dispatcher(self, *args: str, plumbing_dir: str | None = None):
        env = dict(os.environ)
        if plumbing_dir is not None:
            # Point the run at a sandbox so it can't read this repo's real .git-plumbing, and
            # stub sudo so the stamping step can't touch /run — `vscode` has passwordless
            # sudo in this repo's own devcontainer, so the default would really write there.
            env["PLUMBING_DIR"] = plumbing_dir
            env["PLUMBING_WORKSPACE"] = plumbing_dir
            fake_bin = Path(plumbing_dir) / "bin"
            fake_bin.mkdir(exist_ok=True)
            sudo = fake_bin / "sudo"
            sudo.write_text('#!/bin/sh\necho "$@" >>"$(dirname "$0")/../sudo.log"\n')
            sudo.chmod(0o755)
            env["PATH"] = f"{fake_bin}:{env['PATH']}"
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

    def test_both_verbs_run_to_completion_and_stamp(self):
        for verb in ("post-create", "post-start"):
            with self.subTest(verb=verb), _tmpdir() as d:
                result = self._run_dispatcher(verb, plumbing_dir=d)
                self.assertEqual(result.returncode, 0, result.stderr)
                # The stamp is what a consumer's smoke test asserts on, so pin that the verb
                # reaches it and names the file after itself.
                log = (Path(d) / "sudo.log").read_text()
                self.assertIn("install -d -m 0755 /run/devcontainer-plumbing", log)
                self.assertIn(f"touch /run/devcontainer-plumbing/{verb}.stamp", log)

    def test_library_is_found_beside_the_script(self):
        # The in-repo layout. The image layout uses the absolute fallback path; CI's base-image
        # smoke test covers that one, since it needs the file installed under /usr/local.
        with _tmpdir() as d:
            result = self._run_dispatcher("post-create", plumbing_dir=d)
        self.assertNotIn("cannot locate lib.sh", result.stderr)


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
