"""Guards how devcontainer.yml answers an unpublished base-image pin.

The `pin` probe in `changes` reports whether GHCR serves the pinned digest. When it does not, the
required checks build and test the base from the tree, and the advisory `Base image pin published`
goes red. Two failures here pass every check while hiding the state they exist to show:

  - **The probe or the advisory check reads a failure as a pass.** A publish that never landed
    then shows nowhere until someone's `devcontainer up` cannot pull the pin.
  - **A gate stops reading the probe.** An unpublished pin then builds against an image GHCR does
    not serve and fails on the pull, or skips the base build that is the merge's evidence.

The shells are run against stub `docker` and `python3` rather than matched, for the reason
//meta/scripts:test_devcontainer_required_checks gives; those two always stand in, since they play
the registry and the pin. `timeout` is the host's own wherever it has one, and a strict stand-in
only where it does not, so a host with GNU coreutils still checks the real invocation.
`TimeoutStubTest` holds the stand-in to the real tool on those hosts. The `if:` conditions have no
runnable equivalent and are compared.
"""

import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml
from meta.scripts._workflows import job_condition

# Not .resolve(): the workflow is a cross-package data dep, so it lives in the runfiles tree beside
# this file rather than at the source path a resolved symlink would lead back to.
_WORKFLOW = Path(__file__).parent.parent.parent / ".github/workflows/devcontainer.yml"
_JOBS = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))["jobs"]

_REPO = "ghcr.io/syndic/unnatural_designs-devcontainer-base"
_PIN = "sha256:" + "a" * 64
_OTHER = "sha256:" + "b" * 64

# The condition under which a consumer's base is the tree's own rather than the published pin.
_FROM_TREE = "needs.changes.outputs.pin != 'published'"
_PIN_VALUE_RE = re.compile(r"needs\.changes\.outputs\.pin\s*[!=]=\s*'([^']*)'")

# Answers only the one invocation the probe may read the pin through, so a second parser (a grep
# of the Dockerfile) fails here instead of drifting from the one `publish` uses.
_PYTHON3_STUB = """#!/bin/bash
if [ "$*" = "meta/scripts/sync_base_image_pin.py --print-pinned" ] && [ -n "$STUB_PIN" ]; then
  echo "$STUB_PIN"
  exit 0
fi
echo "unexpected python3 call: $*" >&2
exit 97
"""

# `serves` echoes the requested digest back, as `imagetools inspect` does for a digest reference.
_DOCKER_STUB = """#!/bin/bash
echo "$*" >> "$STUB_DOCKER_LOG"
case "$STUB_DOCKER" in
  serves) ref="${!#}"; echo "${ref#*@}" ;;
  other) echo "$STUB_OTHER" ;;
  hangs) exit 124 ;;
  *) echo "ERROR: ${!#}: not found" >&2; exit 1 ;;
esac
"""


# GNU timeout's contract, as far as the probe uses it: a duration then a command, exit 125 on a
# duration it cannot parse, and the command's own status otherwise. Options are not modelled, so a
# probe that starts passing one fails here until this learns it. It enforces no limit.
_TIMEOUT_STUB = r"""#!/bin/bash
if [ $# -lt 2 ] || ! [[ "$1" =~ ^([0-9]+(\.[0-9]*)?|\.[0-9]+)[smhd]?$ ]]; then
  echo "timeout: invalid time interval '$1'" >&2
  exit 125
fi
shift
"$@"
"""


def install(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def step(job: str, predicate) -> dict:
    for candidate in _JOBS[job]["steps"]:
        if predicate(candidate):
            return candidate
    raise AssertionError(f"no matching step in `{job}`")


def probe_step() -> dict:
    return step("changes", lambda s: s.get("id") == "pin")


def build_step() -> dict:
    return step(
        "build-and-smoke-test", lambda s: str(s.get("uses", "")).startswith("devcontainers/ci@")
    )


class Run:
    """One execution of a step's shell, with stub tools and the runner's output files."""

    def __init__(self, script: str, env: dict[str, str], docker: str = "serves", pin: str = _PIN):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            install(bin_dir, "python3", _PYTHON3_STUB)
            install(bin_dir, "docker", _DOCKER_STUB)
            if shutil.which("timeout") is None:
                install(bin_dir, "timeout", _TIMEOUT_STUB)
            output, log = root / "output", root / "docker.log"
            output.touch()
            log.touch()
            done = subprocess.run(
                ["bash", "-c", script],
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    **env,
                    "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}",
                    "RUNNER_TEMP": tmp,
                    "GITHUB_OUTPUT": str(output),
                    "STUB_PIN": pin,
                    "STUB_OTHER": _OTHER,
                    "STUB_DOCKER": docker,
                    "STUB_DOCKER_LOG": str(log),
                },
            )
            self.returncode = done.returncode
            self.stdout = done.stdout
            self.log = f"{done.stdout}{done.stderr}".strip()
            self.outputs = dict(
                line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines()
            )
            self.docker_calls = log.read_text(encoding="utf-8").splitlines()


def probe(base_edited: str, **kwargs) -> Run:
    return Run(probe_step()["run"], {"BASE_EDITED": base_edited}, **kwargs)


def advisory(pin: str) -> Run:
    script = step("pin-published", lambda s: "run" in s)["run"]
    return Run(script, {"PIN": pin})


class ProbeTest(unittest.TestCase):
    """Each answer the registry can give, and the state the probe reports for it."""

    def assert_state(self, run: Run, state: str):
        self.assertEqual(run.returncode, 0, run.log)
        self.assertEqual(run.outputs, {"pin": state})

    def test_a_change_moving_the_pin_is_not_probed(self):
        """Its image publishes on merge, so an unresolvable pin there is expected, not a failure."""
        run = probe("true")
        self.assert_state(run, "edited")
        self.assertEqual(run.docker_calls, [])

    def test_a_served_pin_is_published(self):
        run = probe("false", docker="serves")
        self.assert_state(run, "published")
        self.assertEqual(len(run.docker_calls), 1)
        self.assertTrue(run.docker_calls[0].startswith("buildx imagetools inspect"))
        self.assertTrue(run.docker_calls[0].endswith(f"{_REPO}@{_PIN}"))

    def test_a_missing_pin_is_unpublished_and_logs_the_registry_response(self):
        run = probe("false", docker="missing")
        self.assert_state(run, "unpublished")
        self.assertIn(f"{_REPO}@{_PIN}", run.stdout)
        self.assertIn("not found", run.stdout)

    def test_a_pin_resolving_to_another_digest_is_unpublished(self):
        self.assert_state(probe("false", docker="other"), "unpublished")

    def test_a_timeout_is_unpublished_and_says_so(self):
        """A `timeout` kill writes no stderr, so the exit status is the only thing naming it."""
        run = probe("false", docker="hangs")
        self.assert_state(run, "unpublished")
        self.assertIn("(timed out)", run.stdout)

    def test_an_unreadable_pin_fails_rather_than_reporting_a_state(self):
        run = probe("false", pin="")
        self.assertNotEqual(run.returncode, 0, run.log)
        self.assertEqual(run.outputs, {})
        self.assertEqual(run.docker_calls, [])

    def test_an_unexpected_classification_fails(self):
        for value in ("", "True", "1"):
            with self.subTest(base_edited=value):
                run = probe(value)
                self.assertNotEqual(run.returncode, 0, run.log)
                self.assertEqual(run.outputs, {})


class TimeoutStubTest(unittest.TestCase):
    """The stand-in answers as GNU timeout does, checked wherever the real one exists."""

    _CASES = (
        (["60", "true"], "a whole number of seconds"),
        (["1.5m", "sh", "-c", "exit 7"], "a fractional duration with a unit"),
        (["60", "false"], "the command's failure passed through"),
        (["sixty", "true"], "a duration it cannot parse"),
        (["", "true"], "an empty duration"),
        (["-5", "true"], "an option it does not model"),
        (["60", "no-such-command-anywhere"], "a command that does not exist"),
    )

    def test_the_stub_matches_the_real_tool(self):
        real = shutil.which("timeout")
        if real is None:
            self.skipTest("no real timeout on this host to compare the stand-in against")
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp) / "timeout"
            install(Path(tmp), "timeout", _TIMEOUT_STUB)
            for args, case in self._CASES:
                with self.subTest(case=case):
                    want = subprocess.run([real, *args], capture_output=True).returncode
                    got = subprocess.run([str(stub), *args], capture_output=True).returncode
                    self.assertEqual(got, want, f"timeout {' '.join(args)!r}")


class AdvisoryCheckTest(unittest.TestCase):
    """`Base image pin published`: red on exactly the state the probe cannot vouch for."""

    def test_published_and_edited_pass(self):
        for state in ("published", "edited"):
            with self.subTest(pin=state):
                run = advisory(state)
                self.assertEqual(run.returncode, 0, run.log)

    def test_unpublished_fails_and_names_the_republish(self):
        run = advisory("unpublished")
        self.assertNotEqual(run.returncode, 0)
        errors = [line for line in run.stdout.splitlines() if line.startswith("::error")]
        self.assertEqual(len(errors), 1, run.log)
        self.assertIn("run the Devcontainer workflow on main", errors[0])

    def test_anything_else_fails(self):
        """Empty is what a failed `changes` leaves, and is not evidence of a published pin."""
        for state in ("", "Published", "unknown"):
            with self.subTest(pin=state):
                self.assertNotEqual(advisory(state).returncode, 0)

    def test_it_reads_the_probe(self):
        check = step("pin-published", lambda s: "run" in s)
        self.assertEqual(check.get("env", {}).get("PIN"), "${{ needs.changes.outputs.pin }}")


class WiringTest(unittest.TestCase):
    """What reads the probe. A break here passes every check above."""

    def test_every_state_compared_is_one_the_probe_reports(self):
        """A misspelled state is never equal, so a gate on it silently takes its other branch."""
        reported = {
            probe(edited, docker=docker).outputs["pin"]
            for edited, docker in (("true", "serves"), ("false", "serves"), ("false", "missing"))
        }
        compared = set(_PIN_VALUE_RE.findall(_WORKFLOW.read_text(encoding="utf-8")))
        self.assertTrue(compared, f"nothing in {_WORKFLOW.name} reads the probe")
        self.assertEqual(sorted(compared - reported), [])

    def test_the_base_matrix_and_the_consumer_load_share_one_condition(self):
        """The matrix is the merge's evidence for the base the consumer is built on."""
        load_steps = [
            s
            for s in _JOBS["build-and-smoke-test"]["steps"]
            if s.get("uses") == "./.github/actions/setup-bazel-remote"
            or "//meta/devcontainer-base:load" in s.get("run", "")
        ]
        self.assertEqual(len(load_steps), 2, "expected the Bazel setup and the `:load` step")
        self.assertEqual(job_condition(_JOBS["base-image"]), _FROM_TREE)
        for load in load_steps:
            with self.subTest(step=load.get("name") or load.get("uses")):
                self.assertEqual(load.get("if"), _FROM_TREE)

    def test_the_build_uses_the_pin_only_when_it_is_published(self):
        self.assertEqual(
            build_step().get("env", {}).get("DEVCONTAINER_BASE_IMAGE"),
            "${{ needs.changes.outputs.pin == 'published' && 'pinned-base' "
            "|| 'devcontainer-base:ci' }}",
        )

    def test_an_unpublished_pin_builds_the_consumer_whatever_the_change_touched(self):
        """Otherwise a PR touching nothing in the devcontainer merges with no evidence at all."""
        self.assertIn("needs.changes.outputs.pin == 'unpublished'", build_step().get("if", ""))

    def test_a_manual_run_takes_the_base_path_on_main(self):
        """The republish: base and consumer built and smoke-tested, then published."""
        classify = step("changes", lambda s: s.get("id") == "classify")["run"]
        run = Run(classify.replace("${{ github.event_name }}", "workflow_dispatch"), {})
        self.assertEqual(run.returncode, 0, run.log)
        self.assertEqual(run.outputs, {"changed": "true", "base": "true"})
        self.assertIn(
            "github.event_name == 'workflow_dispatch'", job_condition(_JOBS["publish"]) or ""
        )


if __name__ == "__main__":
    unittest.main()
