"""Tests for base_image_served.py: what counts as "GHCR serves the pin", and how each answer exits.

The exit status is the contract both workflow callers branch on, so every case asserts it: 0 for
served, NOT_SERVED for every way of not being served, and an exception for a tree whose pin cannot
be read, which no caller may mistake for either answer.
"""

import os
import stat
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock

from meta.scripts import base_image_served as subject
from meta.scripts.sync_base_image_pin import BASE_REPOSITORY

_PIN = "sha256:" + "a" * 64
_OTHER = "sha256:" + "b" * 64
_DOCKERFILE = f"ARG BASE_IMAGE=pinned-base\nFROM {BASE_REPOSITORY}:latest@{_PIN} AS pinned-base\n"


class Fixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.dockerfile = self.tmp / "Dockerfile"
        self.dockerfile.write_text(_DOCKERFILE, encoding="utf-8")
        self.asked: list[str] = []

    def run_main(self, answer: subject.Inspection, *argv: str) -> tuple[int, str]:
        def inspector(ref: str) -> subject.Inspection:
            self.asked.append(ref)
            return answer

        out = StringIO()
        with mock.patch("sys.stdout", out):
            code = subject.main([*argv, "--dockerfile", str(self.dockerfile)], inspector)
        return code, out.getvalue()


class MainTest(Fixture):
    def test_the_pinned_digest_served_is_zero(self):
        code, out = self.run_main(subject.Inspection(0, _PIN, ""))
        self.assertEqual(code, 0)
        self.assertEqual(self.asked, [f"{BASE_REPOSITORY}@{_PIN}"])
        self.assertIn("is served", out)

    def test_a_tag_is_asked_about_by_name_and_must_resolve_to_the_pin(self):
        code, _ = self.run_main(subject.Inspection(0, _PIN, ""), ":latest")
        self.assertEqual(code, 0)
        self.assertEqual(self.asked, [f"{BASE_REPOSITORY}:latest"])

    def test_a_tag_left_on_another_digest_is_not_served(self):
        code, out = self.run_main(subject.Inspection(0, _OTHER, ""), ":latest")
        self.assertEqual(code, subject.NOT_SERVED)
        self.assertIn(_OTHER, out)
        self.assertIn(_PIN, out)

    def test_a_failed_inspect_is_not_served_and_keeps_its_stderr(self):
        code, out = self.run_main(subject.Inspection(1, "", "ERROR: not found\n"))
        self.assertEqual(code, subject.NOT_SERVED)
        self.assertIn("exited 1", out)
        self.assertIn("ERROR: not found", out)

    def test_a_timed_out_inspect_says_so(self):
        """A killed process leaves no stderr, so the line itself has to name the cause."""
        code, out = self.run_main(subject.Inspection(None, "", ""))
        self.assertEqual(code, subject.NOT_SERVED)
        self.assertIn("timed out", out)

    def test_an_unreadable_pin_is_an_error_rather_than_an_answer(self):
        self.dockerfile.write_text("FROM debian\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.run_main(subject.Inspection(0, _PIN, ""))
        self.assertEqual(self.asked, [])

    def test_a_tag_must_start_with_a_colon(self):
        with self.assertRaises(SystemExit) as raised, mock.patch("sys.stderr", StringIO()):
            self.run_main(subject.Inspection(0, _PIN, ""), "latest")
        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(self.asked, [])


class InspectTest(unittest.TestCase):
    """The real subprocess call, against a stub `docker` standing in for the registry."""

    def run_inspect(self, body: str) -> tuple[subject.Inspection, list[str]]:
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp) / "docker"
            log = Path(tmp) / "argv"
            stub.write_text(f'#!/bin/bash\necho "$*" > "{log}"\n{body}\n', encoding="utf-8")
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
            path = f"{tmp}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}"
            with mock.patch.dict(os.environ, {"PATH": path}):
                result = subject.inspect(f"{BASE_REPOSITORY}@{_PIN}")
            return result, log.read_text(encoding="utf-8").split() if log.exists() else []

    def test_it_asks_imagetools_for_the_manifest_digest(self):
        result, argv = self.run_inspect(f"echo {_PIN}")
        self.assertEqual(result, subject.Inspection(0, _PIN, ""))
        self.assertEqual(
            argv,
            [
                "buildx",
                "imagetools",
                "inspect",
                "--format",
                "{{.Manifest.Digest}}",
                f"{BASE_REPOSITORY}@{_PIN}",
            ],
        )

    def test_it_keeps_the_status_and_stderr_of_a_failure(self):
        result, _ = self.run_inspect("echo 'ERROR: not found' >&2; exit 1")
        self.assertEqual(result, subject.Inspection(1, "", "ERROR: not found\n"))

    def test_a_stalled_registry_is_cut_off(self):
        with mock.patch.object(subject, "_INSPECT_TIMEOUT_SECONDS", 0.5):
            result, _ = self.run_inspect("exec sleep 5")
        self.assertIsNone(result.returncode)


if __name__ == "__main__":
    unittest.main()
