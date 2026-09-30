"""Guards the step in devcontainer.yml that picks which base image the consumer builds FROM.

`build-and-smoke-test` builds against the pinned digest when the registry serves it, and against
the tree's own base (`//meta/devcontainer-base:load`) when the change edits the base or the pin is
not servable yet. The second case is the gap between a base-changing merge and `publish` pushing
the image, when a correct tree otherwise fails on a `docker pull` of its own pin.

Two ways this goes wrong:

  - **The fallback goes quiet.** A pin that stays unservable is the half-tagged publish the verify
    step in `publish` exists to catch. Building past it with no warning hides that on every PR,
    and nothing fails.
  - **The build stops reading the choice.** `devcontainer.json` supplies `pinned-base` when
    `DEVCONTAINER_BASE_IMAGE` is unset, so a build step that drops the env entry sends a
    base-editing change to the pin: green, and the change never smoke-tested. A misspelled output
    or a skipped choice step passes an empty value instead, which surfaces only as a Docker error
    minutes into the build.

The step's shell is run against stub `docker` and `python3` rather than matched, for the reason
//meta/scripts:test_devcontainer_required_checks gives.
"""

import os
import re
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
_JOB = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))["jobs"]["build-and-smoke-test"]

_STEP_ID = "base"
_REPO = "ghcr.io/syndic/unnatural_designs-devcontainer-base"
_PIN = "sha256:" + "a" * 64
_OTHER = "sha256:" + "b" * 64

_STEP_OUTPUT_RE = re.compile(rf"steps\.{_STEP_ID}\.outputs\.([\w-]+)")

# Answers only the one invocation the step is allowed to read the pin through, so a second parser
# (a grep of the Dockerfile) fails here instead of drifting from the one `publish` uses.
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
  *) echo "ERROR: ${!#}: not found" >&2; exit 1 ;;
esac
"""


def choice_step() -> dict:
    for step in _JOB["steps"]:
        if step.get("id") == _STEP_ID:
            return step
    raise AssertionError(f"no step with id `{_STEP_ID}` in `build-and-smoke-test`")


def build_step() -> dict:
    for step in _JOB["steps"]:
        if str(step.get("uses", "")).startswith("devcontainers/ci@"):
            return step
    raise AssertionError("no devcontainers/ci step in `build-and-smoke-test`")


class Run:
    """One execution of the choice step's shell."""

    def __init__(self, base_edited: str, docker: str = "serves", pin: str = _PIN):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            for name, body in (("python3", _PYTHON3_STUB), ("docker", _DOCKER_STUB)):
                path = bin_dir / name
                path.write_text(body, encoding="utf-8")
                path.chmod(path.stat().st_mode | stat.S_IXUSR)
            output, summary, log = root / "output", root / "summary", root / "docker.log"
            for path in (output, summary, log):
                path.touch()
            env = {
                **os.environ,
                "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}",
                "BASE_EDITED": base_edited,
                "RUNNER_TEMP": tmp,
                "GITHUB_OUTPUT": str(output),
                "GITHUB_STEP_SUMMARY": str(summary),
                "STUB_PIN": pin,
                "STUB_OTHER": _OTHER,
                "STUB_DOCKER": docker,
                "STUB_DOCKER_LOG": str(log),
            }
            done = subprocess.run(
                ["bash", "-c", choice_step()["run"]], capture_output=True, text=True, env=env
            )
            self.returncode = done.returncode
            self.stdout = done.stdout
            self.log = f"{done.stdout}{done.stderr}".strip()
            self.outputs = dict(
                line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines()
            )
            self.summary = summary.read_text(encoding="utf-8")
            self.docker_calls = log.read_text(encoding="utf-8").splitlines()


class ChoiceTest(unittest.TestCase):
    """Each input the step can see, and the base it picks for it."""

    def assert_tree(self, run: Run):
        self.assertEqual(run.returncode, 0, run.log)
        self.assertEqual(run.outputs, {"image": "devcontainer-base:ci", "load": "true"})

    def test_a_base_editing_change_builds_its_own_base_without_probing(self):
        run = Run("true")
        self.assert_tree(run)
        self.assertEqual(run.docker_calls, [], "the pin is irrelevant when the change moves it")
        self.assertNotIn("::warning", run.stdout)

    def test_a_served_pin_is_built_against(self):
        run = Run("false", docker="serves")
        self.assertEqual(run.returncode, 0, run.log)
        self.assertEqual(run.outputs, {"image": "pinned-base", "load": "false"})
        self.assertEqual(len(run.docker_calls), 1)
        self.assertTrue(
            run.docker_calls[0].startswith("buildx imagetools inspect"), run.docker_calls[0]
        )
        self.assertTrue(run.docker_calls[0].endswith(f"{_REPO}@{_PIN}"), run.docker_calls[0])
        self.assertNotIn("::warning", run.stdout)
        self.assertEqual(run.summary, "")

    def test_an_unservable_pin_builds_the_tree_and_says_so(self):
        """A branch that inherited a pin `publish` has not pushed yet."""
        run = Run("false", docker="missing")
        self.assert_tree(run)
        warnings = [line for line in run.stdout.splitlines() if line.startswith("::warning")]
        self.assertEqual(len(warnings), 1, run.log)
        self.assertIn(f"{_REPO}@{_PIN}", warnings[0])
        self.assertIn(f"{_REPO}@{_PIN}", run.summary)
        self.assertIn("not found", run.stdout, "the probe's own stderr is what names the cause")

    def test_a_pin_resolving_to_another_digest_is_not_servable(self):
        run = Run("false", docker="other")
        self.assert_tree(run)
        self.assertIn("::warning", run.stdout)

    def test_an_unreadable_pin_fails_rather_than_falling_back(self):
        """A tree whose pin cannot be parsed is broken; building its own base would hide that."""
        run = Run("false", pin="")
        self.assertNotEqual(run.returncode, 0, run.log)
        self.assertEqual(run.outputs, {})
        self.assertEqual(run.docker_calls, [])

    def test_an_unexpected_classification_fails(self):
        for value in ("", "True", "1"):
            with self.subTest(base_edited=value):
                run = Run(value)
                self.assertNotEqual(run.returncode, 0, run.log)
                self.assertEqual(run.outputs, {})


class WiringTest(unittest.TestCase):
    """What reads the choice, held here rather than left to a Docker error mid-build."""

    def test_the_build_reads_the_chosen_image(self):
        """Without the entry the variable is unset, and devcontainer.json falls back to the pin."""
        self.assertEqual(
            build_step().get("env", {}).get("DEVCONTAINER_BASE_IMAGE"),
            f"${{{{ steps.{_STEP_ID}.outputs.image }}}}",
        )

    def test_every_output_read_is_one_the_step_writes(self):
        """A misspelled output reads as an empty string, which no `if:` or env entry rejects."""
        written = set(Run("true").outputs) | set(Run("false").outputs)
        read = set(_STEP_OUTPUT_RE.findall(_WORKFLOW.read_text(encoding="utf-8")))
        self.assertTrue(read, f"nothing in {_WORKFLOW.name} reads the choice")
        self.assertEqual(sorted(read - written), [])

    def test_the_choice_runs_whenever_the_build_does(self):
        self.assertEqual(
            job_condition(choice_step()),
            job_condition(build_step()),
            "a choice skipped under a build that runs leaves the override empty",
        )

    def test_the_choice_precedes_the_build(self):
        steps = _JOB["steps"]
        self.assertLess(steps.index(choice_step()), steps.index(build_step()))


if __name__ == "__main__":
    unittest.main()
