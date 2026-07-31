"""Tests that the assembled image layers put each file where the dispatcher expects it.

Reads the `pkg_tar` outputs directly rather than running a container, so this needs no Docker
daemon and runs everywhere the rest of the suite does — including this repo's own devcontainer,
which has neither a docker client nor a mounted socket. CI's `docker run` smoke test covers
runtime behaviour; this covers layout, which is where the mistakes have actually been.

The mode assertions are not ceremony: the dispatcher shipped once as 0644, which fails only at
container start with a permission error a long way from its cause.
"""

import sys
import tarfile
import unittest
from pathlib import Path

# Deliberately not .resolve(): these are *generated* data deps, so they live in the runfiles
# tree next to this file. Resolving would follow the runfiles symlink back into the source tree,
# where only the checked-in sources exist.
_HERE = Path(__file__).parent
_DISPATCHER_TAR = _HERE / "dispatcher_layer.tar"
_SCRIPTS_TAR = _HERE / "scripts_layer.tar"

# Where the dispatcher looks for its library, and where consumers call the command from.
_DISPATCHER_PATH = "/usr/local/bin/devcontainer-plumbing"
_LIB_PATH = "/usr/local/share/devcontainer-plumbing/lib.sh"


def _entries(tar_path: Path) -> dict:
    """Map normalised member path -> TarInfo, so assertions don't care about './' prefixes."""
    with tarfile.open(tar_path) as tar:
        return {"/" + m.name.lstrip("./").lstrip("/"): m for m in tar.getmembers() if m.isfile()}


class TestDispatcherLayer(unittest.TestCase):
    def setUp(self):
        self.entries = _entries(_DISPATCHER_TAR)

    def test_installed_on_path_without_the_sh_suffix(self):
        # The source keeps .sh so shellcheck's selector finds it; the command must not.
        self.assertIn(_DISPATCHER_PATH, self.entries)
        self.assertNotIn(f"{_DISPATCHER_PATH}.sh", self.entries)

    def test_is_executable(self):
        self.assertEqual(self.entries[_DISPATCHER_PATH].mode & 0o777, 0o755)

    def test_layer_holds_only_the_dispatcher(self):
        self.assertEqual(list(self.entries), [_DISPATCHER_PATH])


class TestScriptsLayer(unittest.TestCase):
    def setUp(self):
        self.entries = _entries(_SCRIPTS_TAR)

    def test_library_lands_at_the_dispatchers_fallback_path(self):
        # The dispatcher searches beside itself first, then this absolute path. In the image
        # only the second can match, since the two files are installed to different directories.
        self.assertIn(_LIB_PATH, self.entries)

    def test_library_is_not_executable(self):
        # It is sourced, never run.
        self.assertEqual(self.entries[_LIB_PATH].mode & 0o777, 0o644)

    def test_layer_holds_only_the_library(self):
        self.assertEqual(list(self.entries), [_LIB_PATH])


class TestReproducibility(unittest.TestCase):
    def test_timestamps_are_fixed(self):
        # pkg_tar pins mtime so rebuilds are byte-identical and the image digest is stable —
        # the property that makes a Bazel-built image comparable across machines, which a
        # `docker build` (fresh timestamps in the image config) never was.
        for tar_path in (_DISPATCHER_TAR, _SCRIPTS_TAR):
            with self.subTest(tar=tar_path.name):
                mtimes = {m.mtime for m in _entries(tar_path).values()}
                self.assertEqual(len(mtimes), 1)


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
