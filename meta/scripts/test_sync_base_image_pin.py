"""Tests for sync_base_image_pin.py.

The pure functions carry the logic; the freshness assertion against a real built image lives in
`//.devcontainer:test_base_image_pin`, next to the Dockerfile it guards.

The rewrite is the risky half: it edits a file whose `FROM` line is the difference between a
working devcontainer and one that cannot resolve its base at all, so the cases below are mostly
about refusing to guess when the shape is not exactly what is expected.
"""

import json
import sys
import unittest

from meta.scripts.sync_base_image_pin import index_digest, pinned_digest, replace_pin

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


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
