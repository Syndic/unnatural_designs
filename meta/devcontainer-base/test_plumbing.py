"""Tests for the shared devcontainer host-plumbing library.

The plumbing stays shell because the base image it ships in
(`mcr.microsoft.com/devcontainers/base:debian`) has no Python interpreter, and adding one
would cost the RUN-free property that keeps multi-arch builds free. Its decision logic is
therefore factored into side-effect-free functions so it can be exercised from here: each
test sources `host-state-bridge.sh` and calls one function, so nothing privileged ever runs.

Host-stub functions (`initialize_*`) are tested separately, in .devcontainer/, next to the
script that owns them.
"""

import os
import socket
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_BRIDGE_SH = _HERE / "scripts" / "host-state-bridge.sh"
_DISPATCHER = _HERE / "scripts" / "devcontainer-plumbing.sh"

# Source the library, drop it from the argv, then invoke the remaining args as a command.
# host-state-bridge.sh is a pure library — sourcing it runs nothing.
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


# Log the command name as well as its arguments: plumbing_sudo drops the `sudo` entirely when
# already root — which is what happens under Bazel's remote executor, while a developer's
# container and the macOS runner are non-root. One assertion then covers both.
_STUB = '#!/bin/sh\necho "$(basename "$0") $*" >>"$(dirname "$0")/../cmd.log"\n'


def _stub_bin(root: str, *names: str) -> Path:
    """Log-and-succeed stubs for the commands a privileged step would otherwise run for real."""
    fake_bin = Path(root) / "bin"
    fake_bin.mkdir(exist_ok=True)
    for name in names:
        stub = fake_bin / name
        stub.write_text(_STUB)
        stub.chmod(0o755)
    return fake_bin


def _sandbox_env(root: str, **extra: str) -> dict:
    """Env that confines a post-start run to `root`.

    HOME is redirected because the signing bridge writes there for real (`cp`, and a
    `git config --global` this stubs out) — an unredirected run would rewrite the developer's
    own ~/.gitconfig.
    """
    home = Path(root) / "home"
    (home / ".ssh").mkdir(parents=True, exist_ok=True)
    fake_bin = _stub_bin(root, "sudo", "install", "touch", "git", "chown")
    env = dict(os.environ, HOME=str(home))
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env.update(extra)
    return env


def _cmd_log(root: str) -> str:
    log = Path(root) / "cmd.log"
    return log.read_text() if log.exists() else ""


class TestSourcingIsInert(unittest.TestCase):
    """The guard that makes every other test in this file safe."""

    def test_sourcing_produces_no_output_and_no_error(self):
        result = subprocess.run(
            ["bash", "-c", _SOURCE_ONLY, "_", str(_BRIDGE_SH)],
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
        return _sh(_BRIDGE_SH, "plumbing_git_common_action", path, workspace)

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
            return _sh(_BRIDGE_SH, "plumbing_tz_environment", tz, str(envfile)).splitlines()

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
            out = _sh(_BRIDGE_SH, "plumbing_tz_environment", "UTC", f"{d}/nonexistent")
        self.assertEqual(out.splitlines(), ["TZ=UTC"])

    def test_unreadable_file_fails_instead_of_truncating(self):
        # The caller writes this output over the real /etc/environment. A read failure must
        # not look like "the file only needed a TZ line" — that would wipe it. A directory
        # makes grep exit 2 regardless of uid, unlike chmod 000 which root ignores.
        with _tmpdir() as d:
            result = _run(_BRIDGE_SH, "plumbing_tz_environment", "UTC", d)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("TZ=UTC", result.stdout)


class TestGitconfigAction(unittest.TestCase):
    """Who wins the container's ~/.gitconfig: the host snapshot, or whoever got there first."""

    def _action(self, src: str | None, dst: str | None) -> str:
        with _tmpdir() as d:
            paths = []
            for name, content in (("src", src), ("dst", dst)):
                path = Path(d) / name
                if content is not None:
                    path.write_text(content)
                paths.append(str(path))
            return _sh(_BRIDGE_SH, "plumbing_gitconfig_action", *paths)

    def test_snapshot_is_installed_when_the_destination_is_absent(self):
        # The devcontainer CLI path this exists for: nothing bridges the gitconfig.
        self.assertEqual(self._action("[user]\n", None), "install")

    def test_snapshot_is_installed_when_the_destination_is_empty(self):
        self.assertEqual(self._action("[user]\n", ""), "install")

    def test_a_populated_destination_wins(self):
        # VS Code's extension copies its own between postCreate and postStart, so by the time
        # this runs the file is already there and must not be clobbered.
        self.assertEqual(
            self._action("[user]\n  name = host\n", "[user]\n  name = vscode\n"), "skip"
        )

    def test_missing_or_empty_snapshot_is_a_no_op(self):
        # initialize.sh writes an empty file when the host has no ~/.gitconfig; copying it
        # would create an empty ~/.gitconfig that then blocks every later start.
        for src in (None, ""):
            with self.subTest(src=src):
                self.assertEqual(self._action(src, None), "skip")


class TestAgentSocketAction(unittest.TestCase):
    """The guard that keeps the chown to the case Docker Desktop actually creates."""

    def _action(self, path: str) -> str:
        return _sh(_BRIDGE_SH, "plumbing_agent_socket_action", path)

    @contextmanager
    def _socket(self):
        with _tmpdir() as d:
            path = Path(d) / "sock"
            # Bound, not just touched: -S is the first half of the decision.
            sock = socket.socket(socket.AF_UNIX)
            sock.bind(str(path))
            try:
                yield path
            finally:
                sock.close()

    def test_missing_path_is_skipped(self):
        with _tmpdir() as d:
            self.assertEqual(self._action(f"{d}/absent"), "skip")

    def test_regular_file_is_skipped(self):
        # initialize.sh touches a placeholder there on engines that aren't Docker Desktop, so
        # the bind resolves. It is a plain file, and chowning it would be pointless.
        with _tmpdir() as d:
            placeholder = Path(d) / "placeholder"
            placeholder.write_text("")
            self.assertEqual(self._action(str(placeholder)), "skip")

    def test_directory_is_skipped(self):
        with _tmpdir() as d:
            self.assertEqual(self._action(d), "skip")

    def test_socket_we_already_own_is_skipped(self):
        # The second postStart of a container: re-owning is a no-op, so don't pay a sudo.
        with self._socket() as path:
            self.assertEqual(self._action(str(path)), "skip")

    @unittest.skipUnless(os.geteuid() == 0, "only root can hand a socket to another user")
    def test_socket_owned_by_someone_else_is_chowned(self):
        # The case in the container: Docker Desktop mounts it root-owned, remoteUser is not.
        with self._socket() as path:
            os.chown(path, 65534, 65534)
            self.assertEqual(self._action(str(path)), "chown")


class TestInputValidation(unittest.TestCase):
    """The library re-validates what the host stub wrote. The stub lives in the consuming
    repo, not here, and both values feed privileged commands."""

    def test_safe_zone_accepted(self):
        for good in ("UTC", "America/Los_Angeles", "Etc/GMT+5"):
            with self.subTest(value=good):
                self.assertEqual(_rc(_BRIDGE_SH, "plumbing_zone_is_safe", good), 0)

    def test_unsafe_zone_rejected(self):
        for bad in ("", "/etc/shadow", "../../etc/shadow", "America/../.."):
            with self.subTest(value=bad):
                self.assertNotEqual(_rc(_BRIDGE_SH, "plumbing_zone_is_safe", bad), 0)

    def test_absolute_git_path_accepted(self):
        self.assertEqual(_rc(_BRIDGE_SH, "plumbing_git_path_is_safe", "/Users/x/repo/.git"), 0)

    def test_relative_or_traversing_git_path_rejected(self):
        for bad in ("", "repo/.git", "/Users/../etc", "/a/../../b"):
            with self.subTest(value=bad):
                self.assertNotEqual(_rc(_BRIDGE_SH, "plumbing_git_path_is_safe", bad), 0)


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
                    str(_BRIDGE_SH),
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

    def _apply(
        self,
        plumbing_dir: str,
        workspace: str,
        require: str | None = None,
        phase: str | None = "post-create",
        env: dict | None = None,
    ):
        # post-create by default: it is the phase that runs nothing privileged, so a test that
        # doesn't say otherwise stays safe to run against the real environment.
        env = dict(env or os.environ)
        if require is not None:
            env["PLUMBING_REQUIRE_GIT_CHECKOUT"] = require
        args = [plumbing_dir, workspace] + ([] if phase is None else [phase])
        return subprocess.run(
            ["bash", "-c", _SOURCE_AND_CALL, "_", str(_BRIDGE_SH), "plumbing_apply_all", *args],
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

    def test_omitting_the_phase_fails_loudly(self):
        # The phase now selects steps, so defaulting it would silently run the signing bridge
        # — sudo and all — for a caller that never asked for a phase at all.
        with _tmpdir() as d:
            result = self._apply(d, d, phase=None)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("phase is required", result.stderr)


class TestSigningBridge(unittest.TestCase):
    """The four steps of the signing bridge: ~/.ssh mode, gitconfig, signers, agent socket.

    Exercised through plumbing_apply_all so the phase fork is covered too: these run at
    postStart only, because the gitconfig install has to lose to the Dev Containers extension's
    own copy, which lands between the two hooks.
    """

    @contextmanager
    def _bridge(
        self, phase: str, gitconfig: str | None = "[user]\n", signers: str = "ssh-ed25519 AAA\n"
    ):
        with _tmpdir() as d:
            env = _sandbox_env(d)
            home = Path(env["HOME"])
            if gitconfig is not None:
                (Path(d) / "host-gitconfig").write_text(gitconfig)
            (home / ".ssh" / "allowed_signers").write_text(signers)
            result = self._apply_in(d, env, phase)
            self.assertEqual(result.returncode, 0, result.stderr)
            yield home, _cmd_log(d)

    def _apply_in(self, plumbing_dir: str, env: dict, phase: str):
        return subprocess.run(
            [
                "bash",
                "-c",
                _SOURCE_AND_CALL,
                "_",
                str(_BRIDGE_SH),
                "plumbing_apply_all",
                plumbing_dir,
                plumbing_dir,
                phase,
            ],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    def test_post_start_repairs_the_ssh_directory(self):
        # Docker creates the mount parent root-owned 0755; ssh then ignores the whole directory.
        with self._bridge("post-start") as (_, log):
            self.assertIn("install -d -m 700", log)
            self.assertIn(".ssh", log)

    def test_post_start_installs_the_host_gitconfig(self):
        with self._bridge("post-start") as (home, _):
            self.assertEqual((home / ".gitconfig").read_text(), "[user]\n")

    def test_post_start_repoints_the_allowed_signers_file(self):
        with self._bridge("post-start") as (home, log):
            self.assertIn(
                f"git config --global gpg.ssh.allowedSignersFile {home}/.ssh/allowed_signers",
                log,
            )

    def test_an_empty_allowed_signers_file_is_left_alone(self):
        # CI's case: the bind resolves to initialize.sh's empty fallback. Pointing git at it
        # would replace a host-absolute path that at least says what was meant with one that
        # can never verify anything.
        with self._bridge("post-start", signers="") as (_, log):
            self.assertNotIn("allowedSignersFile", log)

    def test_post_create_runs_none_of_it(self):
        # The ordering fact this whole fork exists for.
        with self._bridge("post-create") as (home, log):
            self.assertFalse((home / ".gitconfig").exists())
            self.assertNotIn("install -d", log)
            self.assertNotIn("allowedSignersFile", log)


class TestDispatcher(unittest.TestCase):
    """Argument handling and library resolution — the surface every consumer calls."""

    def _run_dispatcher(self, *args: str, plumbing_dir: str | None = None):
        env = dict(os.environ)
        if plumbing_dir is not None:
            # Point the run at a sandbox so it can't read this repo's real .git-plumbing, and
            # so the stamping step and the post-start signing bridge land on stubs and a
            # throwaway HOME rather than on /run and the developer's own ~/.gitconfig.
            env = _sandbox_env(plumbing_dir, PLUMBING_DIR=plumbing_dir)
            env["PLUMBING_WORKSPACE"] = plumbing_dir
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
                # reaches it and names the file after itself. The log holds either
                # "sudo install …" or "install …" depending on whether the test runs as root.
                log = _cmd_log(d)
                self.assertIn("install -d -m 0755 /run/devcontainer-plumbing", log)
                self.assertIn(f"touch /run/devcontainer-plumbing/{verb}.stamp", log)

    def test_the_verb_selects_the_signing_bridge_not_just_the_stamp_name(self):
        # The dispatcher hands the phase to the library, which is what makes post-start do
        # more than post-create. Passing only the stamp name would leave every consumer's
        # container without its host gitconfig and no error to show for it.
        seen = {}
        for verb in ("post-create", "post-start"):
            with _tmpdir() as d:
                (Path(d) / "host-gitconfig").write_text("[user]\n")
                result = self._run_dispatcher(verb, plumbing_dir=d)
                self.assertEqual(result.returncode, 0, result.stderr)
                seen[verb] = (Path(d) / "home" / ".gitconfig").exists()
        self.assertEqual(seen, {"post-create": False, "post-start": True})

    def test_library_is_found_beside_the_script(self):
        # The in-repo layout. The image layout uses the absolute fallback path; CI's base-image
        # smoke test covers that one, since it needs the file installed under /usr/local.
        with _tmpdir() as d:
            result = self._run_dispatcher("post-create", plumbing_dir=d)
        self.assertNotIn("cannot locate host-state-bridge.sh", result.stderr)


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
