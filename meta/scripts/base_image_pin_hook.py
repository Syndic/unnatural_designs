#!/usr/bin/env python3
"""pre-commit entry for `base-image-pin`: gate on the shared rule set, then re-derive the pin.

The hook used to carry its own `files:` regex naming the base image's inputs — a third copy of a
set that also lives in two workflows' classification rules, with nothing failing when they
disagreed. pre-commit's `files:` cannot read a shared definition, so the hook now takes no filter
at all and asks `.github/path-rules.toml` here instead.

The cost of that shape, accepted deliberately: pre-commit invokes this on every commit, and may
split a long filename list across several invocations, each deciding independently. Both actions
this runs are idempotent and the second invocation hits a warm Bazel cache, so a repeat costs a
process spawn rather than a rebuild.

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

from meta.scripts._path_rules import load_rules
from meta.scripts.sync_base_image_pin import main as sync_pin

_WORKSPACE = Path(__file__).resolve().parents[2]
_DEFAULT_RULES = _WORKSPACE / ".github" / "path-rules.toml"

# The set of everything the image is assembled from. Named rather than spelled out: that is the
# entire point of the shared file.
_RULE = "base"

_IMAGE_TARGET = "//meta/devcontainer-base:image"


def matching(files: list[str], pattern: str) -> list[str]:
    """The staged paths that are inputs to the base image, in the order given."""
    return [f for f in files if re.search(pattern, f)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", help="Staged paths, as pre-commit passes them.")
    parser.add_argument("--rules-file", type=Path, default=_DEFAULT_RULES)
    args = parser.parse_args(argv)

    pattern = load_rules(args.rules_file)[_RULE]
    hits = matching(args.files, pattern)
    if not hits:
        return 0

    print(f"base image inputs staged ({', '.join(hits)}); rebuilding to re-derive the pin.")
    # The sync reads this build's output; it does not run Bazel itself.
    subprocess.run(["bazel", "build", _IMAGE_TARGET], check=True)
    return sync_pin([])


if __name__ == "__main__":
    sys.exit(main())
