#!/usr/bin/env python3
"""Sync `.devcontainer/Dockerfile`'s base-image pin with the image this repo builds.

The pin is a **derived file**, not a dependency. `pkg_tar` fixes timestamps and `oci_push`
publishes the exact index Bazel assembled, so the digest a merge will publish is a pure function
of the source tree — knowable in the PR that changes the image, before anything is pushed. Left
as a dependency it would instead take a publish plus a Renovate round-trip to catch up, and the
pin would name a superseded image the whole time.

The consequence to know: between a merge and the publish job finishing, and on any branch that
edits `meta/devcontainer-base/`, the pinned digest names an image that is not in the registry yet.
Point the override at the last published one to get a working container meanwhile:

    DEVCONTAINER_BASE_IMAGE=ghcr.io/syndic/unnatural_designs-devcontainer-base:latest \
      devcontainer up --workspace-folder .

You then run the *previous* plumbing locally, which is fine — CI builds against the candidate image
(`bazel run :load`, which needs a Docker daemon this devcontainer deliberately lacks) and is what
validates the new one.

Usage (the build has to come first — this reads its output, it does not run Bazel):

    bazel build //meta/devcontainer-base:image
    python3 meta/scripts/sync_base_image_pin.py            # rewrite the pin
    python3 meta/scripts/sync_base_image_pin.py --check    # exit 1 if it is stale

`renovate-derived-files.yml` runs the rewrite when MODULE.bazel moves, since the upstream base it
pins feeds this digest; `//.devcontainer:test_base_image_pin` is the check that keeps a hand edit
from drifting.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_BASE_REPOSITORY = "ghcr.io/syndic/unnatural_designs-devcontainer-base"

# The one pinned reference in the Dockerfile: `FROM <repo>:<tag>@sha256:<digest> AS <alias>`.
# Anchored on the repository so the uv `COPY --from=` reference can never match.
_PIN_RE = re.compile(
    rf"(?m)^(?P<head>FROM\s+{re.escape(_BASE_REPOSITORY)}:[\w][\w.-]*@)"
    r"(?P<digest>sha256:[0-9a-f]{64})"
)

_DEFAULT_DOCKERFILE = Path(".devcontainer/Dockerfile")
# `bazel build` leaves the OCI layout under the convenience symlink.
_DEFAULT_LAYOUT = Path("bazel-bin/meta/devcontainer-base/image")


# ── Pure functions (the part the tests exercise) ──────────────────────────────


def index_digest(index_json: str) -> str:
    """Return the image index's own digest from an OCI layout's `index.json`.

    `oci_image_index` nests the real manifest list one level down, so the digest to pin is the
    single entry in the layout index — the same indirection `test_image_layers.py` walks.
    """
    manifests = json.loads(index_json)["manifests"]
    if len(manifests) != 1:
        raise ValueError(f"expected exactly one manifest in the layout index, got {len(manifests)}")
    return manifests[0]["digest"]


def pinned_digest(dockerfile: str) -> str:
    """Return the digest currently pinned in the Dockerfile."""
    return _match(dockerfile).group("digest")


def replace_pin(dockerfile: str, digest: str) -> str:
    """Return the Dockerfile with its pin set to `digest`. Idempotent."""
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError(f"not a sha256 digest: {digest!r}")
    match = _match(dockerfile)
    return dockerfile[: match.start("digest")] + digest + dockerfile[match.end("digest") :]


def _match(dockerfile: str) -> re.Match[str]:
    """The single pinned `FROM` — anything else means the shape moved and the caller must stop."""
    matches = list(_PIN_RE.finditer(dockerfile))
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one pinned `FROM {_BASE_REPOSITORY}:<tag>@sha256:…` line, "
            f"found {len(matches)}"
        )
    return matches[0]


# ── Driver ────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dockerfile", type=Path, default=_DEFAULT_DOCKERFILE)
    parser.add_argument(
        "--layout-dir",
        type=Path,
        default=_DEFAULT_LAYOUT,
        help="OCI layout directory built by //meta/devcontainer-base:image.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report staleness and exit 1 instead of rewriting.",
    )
    args = parser.parse_args(argv)

    index = args.layout_dir / "index.json"
    if not index.exists():
        print(
            f"{index} not found — run `bazel build //meta/devcontainer-base:image` first.",
            file=sys.stderr,
        )
        return 2

    built = index_digest(index.read_text(encoding="utf-8"))
    dockerfile = args.dockerfile.read_text(encoding="utf-8")
    current = pinned_digest(dockerfile)

    if current == built:
        print(f"{args.dockerfile} already pins the built image ({built}).")
        return 0

    if args.check:
        print(f"{args.dockerfile} pins {current}, but this tree builds {built}.", file=sys.stderr)
        print(
            "Run `bazel build //meta/devcontainer-base:image && "
            "python3 meta/scripts/sync_base_image_pin.py` and commit the result.",
            file=sys.stderr,
        )
        return 1

    args.dockerfile.write_text(replace_pin(dockerfile, built), encoding="utf-8")
    print(f"{args.dockerfile}: {current} -> {built}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
