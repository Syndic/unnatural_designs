"""Tests for sync_base_image_pin.py.

The pure functions carry the logic; the freshness assertion against a real built image lives in
`//.devcontainer:test_base_image_pin`, next to the Dockerfile it guards.

The rewrite is the risky half: it edits a file whose `FROM` line is the difference between a
working devcontainer and one that cannot resolve its base at all, so the cases below are mostly
about refusing to guess when the shape is not exactly what is expected.
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from meta.scripts.sync_base_image_pin import index_digest, main, pinned_digest, replace_pin

_REPO = "ghcr.io/syndic/unnatural_designs-devcontainer-base"
_OLD = "sha256:" + "a" * 64
_NEW = "sha256:" + "b" * 64

_DOCKERFILE = f"""# comment
ARG BASE_IMAGE=pinned-base
FROM {_REPO}:latest@{_OLD} AS pinned-base
FROM ${{BASE_IMAGE}}

COPY --from=ghcr.io/astral-sh/uv:0.12.0 /uv /uvx /usr/local/bin/
"""


class TestIndexDigest(unittest.TestCase):
    def test_reads_the_single_layout_entry(self):
        layout = json.dumps({"schemaVersion": 2, "manifests": [{"digest": _NEW, "size": 1}]})
        self.assertEqual(index_digest(layout), _NEW)

    def test_rejects_a_layout_with_several_entries(self):
        # `oci_image_index` nests the manifest list one level down, so the layout index holds
        # exactly one entry. More than one means the target's shape changed and picking [0]
        # would silently pin whichever happened to be first.
        layout = json.dumps({"manifests": [{"digest": _NEW}, {"digest": _OLD}]})
        with self.assertRaises(ValueError):
            index_digest(layout)


class TestPinnedDigest(unittest.TestCase):
    def test_reads_the_current_pin(self):
        self.assertEqual(pinned_digest(_DOCKERFILE), _OLD)

    def test_ignores_the_uv_copy_reference(self):
        # `COPY --from=` names another image entirely; anchoring on the repository is what keeps
        # a future digest-pinned COPY from being mistaken for the base pin.
        self.assertNotIn("astral-sh", pinned_digest(_DOCKERFILE))

    def test_rejects_a_dockerfile_with_no_pin(self):
        with self.assertRaises(ValueError):
            pinned_digest("FROM debian:bookworm\n")

    def test_rejects_a_dockerfile_with_two_pins(self):
        # Two candidates means rewriting one of them is a coin flip.
        doubled = _DOCKERFILE + f"FROM {_REPO}:latest@{_NEW} AS other\n"
        with self.assertRaises(ValueError):
            pinned_digest(doubled)


class TestReplacePin(unittest.TestCase):
    def test_replaces_only_the_digest(self):
        result = replace_pin(_DOCKERFILE, _NEW)
        self.assertIn(f"FROM {_REPO}:latest@{_NEW} AS pinned-base", result)
        self.assertNotIn(_OLD, result)

    def test_leaves_everything_else_byte_identical(self):
        # The alias, the tag, the ARG above it and the consuming FROM below are all load-bearing
        # (see "Consuming the image" in meta/devcontainer-base/README.md); this rewrite must not
        # be the thing that disturbs them.
        self.assertEqual(
            replace_pin(_DOCKERFILE, _NEW).replace(_NEW, _OLD),
            _DOCKERFILE,
        )

    def test_is_idempotent(self):
        once = replace_pin(_DOCKERFILE, _NEW)
        self.assertEqual(replace_pin(once, _NEW), once)

    def test_rejects_a_malformed_digest(self):
        for bad in ("sha256:beef", "b" * 64, f"{_NEW} AS evil", ""):
            with self.subTest(digest=bad), self.assertRaises(ValueError):
                replace_pin(_DOCKERFILE, bad)

    def test_refuses_when_the_pin_is_missing(self):
        with self.assertRaises(ValueError):
            replace_pin("FROM debian:bookworm\n", _NEW)


class TestPrintPinned(unittest.TestCase):
    """The mode the publish job reads. It must answer without a build — that job has no Bazel
    output tree, and a mode that needed one would fail the step for the wrong reason."""

    def _run(self, dockerfile_text: str) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Dockerfile"
            path.write_text(dockerfile_text, encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                # --layout-dir points at nothing on purpose: reaching it would mean the early
                # return is gone and the publish job would start depending on a built image.
                code = main(
                    [
                        "--print-pinned",
                        "--dockerfile",
                        str(path),
                        "--layout-dir",
                        str(Path(tmp) / "no-such-layout"),
                    ]
                )
            return code, out.getvalue().strip()

    def test_prints_the_pinned_digest_without_a_build(self):
        self.assertEqual(self._run(_DOCKERFILE), (0, _OLD))

    def test_refuses_a_dockerfile_it_cannot_parse(self):
        # Must raise rather than print nothing: the job compares this against the registry, and
        # an empty answer would compare two blanks and pass.
        with self.assertRaises(ValueError):
            self._run("FROM debian:bookworm\n")


class TestRewritePath(unittest.TestCase):
    """`main()`'s mutating half — what the pre-commit hook and renovate-derived-files.yml run.

    The pure functions above cover the parsing; this covers the driver that decides whether to
    touch the file at all. The missing-layout case is the one with no backstop anywhere: a
    `write_text` firing on a half-built tree would pin a digest nothing published.
    """

    def _tree(self, tmp: str, digest: str | None) -> tuple[Path, Path]:
        dockerfile = Path(tmp) / "Dockerfile"
        dockerfile.write_text(_DOCKERFILE, encoding="utf-8")
        layout = Path(tmp) / "layout"
        if digest is not None:
            layout.mkdir()
            (layout / "index.json").write_text(
                json.dumps({"manifests": [{"digest": digest}]}), encoding="utf-8"
            )
        return dockerfile, layout

    def _run(self, dockerfile: Path, layout: Path) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = main(["--dockerfile", str(dockerfile), "--layout-dir", str(layout)])
        return code, out.getvalue()

    def test_rewrites_the_pin_to_the_built_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            dockerfile, layout = self._tree(tmp, _NEW)
            code, _ = self._run(dockerfile, layout)
            self.assertEqual(code, 0)
            self.assertEqual(pinned_digest(dockerfile.read_text(encoding="utf-8")), _NEW)

    def test_rerunning_is_a_no_op(self):
        # The hook runs on every matching commit, so "already correct" must not be an error —
        # and must not rewrite, or every commit would carry a spurious Dockerfile diff.
        with tempfile.TemporaryDirectory() as tmp:
            dockerfile, layout = self._tree(tmp, _OLD)
            before = dockerfile.read_bytes()
            code, _ = self._run(dockerfile, layout)
            self.assertEqual(code, 0)
            self.assertEqual(dockerfile.read_bytes(), before)

    def test_missing_layout_refuses_without_touching_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            dockerfile, layout = self._tree(tmp, None)
            before = dockerfile.read_bytes()
            code, out = self._run(dockerfile, layout)
            self.assertEqual(code, 2)
            self.assertIn("bazel build", out)
            self.assertEqual(dockerfile.read_bytes(), before)


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
