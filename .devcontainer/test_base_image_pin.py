"""Asserts the Dockerfile pins the base image this tree actually builds.

The pin is derived, not depended on: the digest is a pure function of `meta/devcontainer-base/`
plus the upstream base in `MODULE.bazel`, so a change to either restales it in the same commit.
This is the guard that says so — the same role `bazel mod tidy` plays for `MODULE.bazel.lock`,
except it can be a plain test because the digest falls out of an already-built artifact and needs
no daemon.

Without it, a stale pin is invisible: the devcontainer still builds, from the previous image, and
the difference only shows up as plumbing that mysteriously predates your change.
"""

import sys
import unittest
from pathlib import Path

from meta.scripts.sync_base_image_pin import index_digest, pinned_digest

# Not .resolve(): the image is a generated cross-package data dep, so it lives in the runfiles
# tree beside this file rather than in the source tree a resolved symlink leads back to.
_HERE = Path(__file__).parent
_DOCKERFILE = _HERE / "Dockerfile"
_LAYOUT_INDEX = _HERE.parent / "meta" / "devcontainer-base" / "image" / "index.json"


class TestBaseImagePinIsFresh(unittest.TestCase):
    def test_pin_matches_the_built_image(self):
        built = index_digest(_LAYOUT_INDEX.read_text(encoding="utf-8"))
        current = pinned_digest(_DOCKERFILE.read_text(encoding="utf-8"))
        self.assertEqual(
            current,
            built,
            "The devcontainer's base-image pin is stale. Run:\n"
            "  bazel build //meta/devcontainer-base:image && "
            "python3 meta/scripts/sync_base_image_pin.py\n"
            "and commit .devcontainer/Dockerfile. The digest is reproducible, so this is the "
            "one a merge will publish.",
        )


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
