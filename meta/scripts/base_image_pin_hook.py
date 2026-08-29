#!/usr/bin/env python3
"""pre-commit entry for `base-image-pin`: gate on the shared BASE set, then re-derive the pin.

The hook used to carry its own `files:` regex naming the base image's inputs — a third copy of a
set that also drives both workflows' path classification, with nothing failing when they disagreed.
pre-commit's `files:` cannot read a shared definition, so the hook now takes no filter at all and
matches against the shared `BASE` set here instead.

The cost of that shape, accepted deliberately: pre-commit invokes this on every commit, and may
still split a long filename list across several invocations, each deciding independently. Both
actions this runs are idempotent and a repeat hits a warm Bazel cache, so it costs a process spawn
rather than a rebuild. That is only true while those invocations are *sequential*, which is what
the hook's `require_serial` buys — pre-commit otherwise runs partitions in a thread pool, and two
concurrent pin rewrites can interleave a read with a truncating write.

Usage (pre-commit supplies the filenames):

    python3 meta/scripts/base_image_pin_hook.py [<staged path> ...]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Run as a script rather than through `bazel run`, the workspace root is not on sys.path, so
# `from meta.scripts.X` would fail. Adding it explicitly fixes that and is harmless under bazel
# py_binary, where rules_python already makes the import resolvable.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from meta.scripts.path_classification_pattern_sets import BASE
from meta.scripts.sync_base_image_pin import main as sync_pin

_IMAGE_TARGET = "//meta/devcontainer-base:image"


def matching(files: list[str], patterns: tuple[str, ...] = BASE) -> list[str]:
    """The staged paths that are inputs to the base image, in the order given."""
    return [f for f in files if any(re.search(p, f) for p in patterns)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", help="Staged paths, as pre-commit passes them.")
    args = parser.parse_args(argv)

    hits = matching(args.files)
    if not hits:
        return 0

    print(f"base image inputs staged ({', '.join(hits)}); rebuilding to re-derive the pin.")
    # The sync reads this build's output; it does not run Bazel itself.
    subprocess.run(["bazel", "build", _IMAGE_TARGET], check=True)
    return sync_pin([])


if __name__ == "__main__":
    sys.exit(main())
