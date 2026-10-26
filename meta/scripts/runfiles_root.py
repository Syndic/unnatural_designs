"""The repository root as a test sees it: the runfiles tree, deliberately not `.resolve()`d.

Bazel gives a test its data deps as a tree of symlinks, and that tree is the only place a test may
read them from. `Path.resolve()` follows a symlink back to the source checkout, which is wrong in
three ways: a generated data dep (a built image, an assembled layer) does not exist there at all;
under remote execution the checkout is not on the executor; and a checked-in file read from there
is found whether or not the test declared it, so a missing `data` entry stops failing locally and
fails only in CI.

So the root is this module's own location, three levels up and unresolved. Outside Bazel — a test
run straight from the checkout — the same expression names the checkout, which is also correct.

Every test that reads a file by path builds it from `RUNFILES_ROOT`. The ones that read generated
deps, `//meta/devcontainer-base:test_image_layers` and `//.devcontainer:test_base_image_pin`,
are what fail if this ever resolves.
"""

from pathlib import Path
from typing import Final

RUNFILES_ROOT: Final = Path(__file__).parent.parent.parent
