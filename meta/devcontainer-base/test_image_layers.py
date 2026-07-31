"""Tests that the assembled image layers put each file where the dispatcher expects it.

Reads the `pkg_tar` outputs directly rather than running a container, so this needs no Docker
daemon and runs everywhere the rest of the suite does — including this repo's own devcontainer,
which has neither a docker client nor a mounted socket. CI's `docker run` smoke test covers
runtime behaviour; this covers layout, which is where the mistakes have actually been.

The mode assertions are not ceremony: the dispatcher shipped once as 0644, which fails only at
container start with a permission error a long way from its cause.
"""

import hashlib
import json
import posixpath
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


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _entries(tar_path: Path) -> dict:
    """Map normalised member path -> TarInfo, so assertions don't care about './' prefixes."""
    # normpath, not lstrip("./"): lstrip takes a character *set*, so it would turn a member
    # named "./.bashrc" into "/bashrc".
    with tarfile.open(tar_path) as tar:
        return {posixpath.normpath("/" + m.name): m for m in tar.getmembers() if m.isfile()}


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


class _Layout:
    """Minimal reader for the `oci_image_index` output — an OCI layout directory."""

    def __init__(self, root: Path):
        self.root = root
        index = self._blob(json.loads((root / "index.json").read_text())["manifests"][0]["digest"])
        # oci_image_index nests the real manifest list one level down.
        self.manifests = index["manifests"]

    def _blob(self, digest: str) -> dict:
        algo, hexdigest = digest.split(":", 1)
        return json.loads((self.root / "blobs" / algo / hexdigest).read_text())

    def by_arch(self) -> dict:
        return {m["platform"]["architecture"]: self._blob(m["digest"]) for m in self.manifests}

    def config_of(self, manifest: dict) -> dict:
        return self._blob(manifest["config"]["digest"])


class TestIndexWiring(unittest.TestCase):
    """Covers the layer -> image -> index wiring that the per-tar tests can't see.

    Dropping `:scripts_layer` from `oci_image.tars`, or losing a label in a future edit, would
    otherwise pass `bazel test //...` and be caught only by CI's smoke test — and, for the
    arm64 manifest, not even then, because only the host's architecture is ever executed.
    """

    @classmethod
    def setUpClass(cls):
        cls.layout = _Layout(_HERE / "image")
        cls.images = cls.layout.by_arch()

    def test_index_carries_both_platforms(self):
        self.assertEqual(sorted(self.images), ["amd64", "arm64"])

    def test_both_arches_are_linux(self):
        oses = {m["platform"]["os"] for m in self.layout.manifests}
        self.assertEqual(oses, {"linux"})

    def test_every_arch_carries_both_of_our_layers(self):
        # rules_oci stores pkg_tar layers uncompressed, so a layer's digest is just the sha256
        # of the tar we built. Asserting the digests — rather than counting from the end of the
        # list — is what makes a dropped or reordered `tars` entry fail here instead of at
        # container start. It also can't go stale: the expected values are recomputed from the
        # same files the image is built from.
        expected = [_sha256(_SCRIPTS_TAR), _sha256(_DISPATCHER_TAR)]
        for arch, manifest in self.images.items():
            with self.subTest(arch=arch):
                digests = [layer["digest"] for layer in manifest["layers"]]
                self.assertEqual(digests[-2:], expected)

    def test_the_two_arches_differ_only_in_their_base(self):
        # Our layers are two shell scripts, identical everywhere; anything else that diverges
        # between the manifests came from the base.
        tails = {
            arch: [layer["digest"] for layer in manifest["layers"][-2:]]
            for arch, manifest in self.images.items()
        }
        self.assertEqual(tails["amd64"], tails["arm64"])

    def test_our_labels_survive_on_every_arch(self):
        # image.source is what associates the GHCR package with this repo.
        for arch, manifest in self.images.items():
            with self.subTest(arch=arch):
                labels = self.layout.config_of(manifest)["config"]["Labels"]
                self.assertEqual(
                    labels.get("org.opencontainers.image.source"),
                    "https://github.com/Syndic/unnatural_designs",
                )
                self.assertIn("org.opencontainers.image.description", labels)


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
