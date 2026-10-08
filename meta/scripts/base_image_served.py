#!/usr/bin/env python3
"""Ask GHCR whether it serves the shared base image the tree pins.

    python3 meta/scripts/base_image_served.py            # the pinned digest itself
    python3 meta/scripts/base_image_served.py :latest    # a tag, which must resolve to the pin

The one definition of "the registry serves the pin", for `devcontainer.yml`'s probe in `changes`
and `publish`'s verify step. The pin and the repository come from sync_base_image_pin.py, so the
digest has one parser.

Exit status: 0 when the reference resolves to the pinned digest; 3 when it does not, whatever the
reason (absent, another digest, inspect failed or timed out); anything else is an error in this
script, which callers must not read as either answer.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

# Run as a script rather than through `bazel run`, the workspace root is not on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from meta.scripts.sync_base_image_pin import BASE_REPOSITORY, pinned_digest

NOT_SERVED = 3

# Long enough for a slow registry answer, short enough that a stalled one cannot hold the job.
_INSPECT_TIMEOUT_SECONDS = 60

_DEFAULT_DOCKERFILE = Path(".devcontainer/Dockerfile")


class Inspection(NamedTuple):
    """What `docker buildx imagetools inspect` said, or `returncode=None` if it timed out."""

    returncode: int | None
    digest: str
    stderr: str


def inspect(ref: str) -> Inspection:
    try:
        done = subprocess.run(
            ["docker", "buildx", "imagetools", "inspect", "--format", "{{.Manifest.Digest}}", ref],
            capture_output=True,
            text=True,
            timeout=_INSPECT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return Inspection(None, "", "")
    return Inspection(done.returncode, done.stdout.strip(), done.stderr)


def verdict(ref: str, pinned: str, result: Inspection) -> tuple[bool, str]:
    """Whether `result` shows `ref` serving `pinned`, and the line that says so."""
    if result.returncode is None:
        return False, f"{ref} did not resolve: inspect timed out after {_INSPECT_TIMEOUT_SECONDS}s."
    if result.returncode != 0:
        return False, f"{ref} did not resolve: inspect exited {result.returncode}."
    if result.digest != pinned:
        return (
            False,
            f"{ref} resolved to '{result.digest or 'nothing'}', but the tree pins {pinned}.",
        )
    return True, f"{ref} is served."


def main(argv: list[str] | None = None, inspector: Callable[[str], Inspection] = inspect) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "tag",
        nargs="?",
        help="A `:tag` to check instead of the pinned digest, e.g. `:latest`.",
    )
    parser.add_argument("--dockerfile", type=Path, default=_DEFAULT_DOCKERFILE)
    args = parser.parse_args(argv)
    if args.tag is not None and not args.tag.startswith(":"):
        parser.error(f"a tag starts with ':', got {args.tag!r}")

    pinned = pinned_digest(args.dockerfile.read_text(encoding="utf-8"))
    ref = f"{BASE_REPOSITORY}{args.tag or '@' + pinned}"
    result = inspector(ref)
    served, line = verdict(ref, pinned, result)
    print(line)
    if result.stderr:
        print("Its stderr follows:")
        print(result.stderr, end="" if result.stderr.endswith("\n") else "\n")
    return 0 if served else NOT_SERVED


if __name__ == "__main__":
    sys.exit(main())
