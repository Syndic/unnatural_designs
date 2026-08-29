"""The named pattern sets every site in this repo classifies paths against.

One definition each, composed by set union, so no site restates another's patterns. Consumers
name a set: `classify_changed_paths.py` turns a diff into `name=true|false` step outputs for
devcontainer.yml and renovate-derived-files.yml, and `base_image_pin_hook.py` gates the
`base-image-pin` pre-commit hook on `BASE`.

Composition is the point. `BASE` decides whether a new devcontainer base image is published, and
the `BAZEL` set inside it decides whether the consumer's pin in .devcontainer/Dockerfile is
re-derived. Those two have to agree: a path that re-derives the pin without triggering a publish
writes a digest no job ever pushes — a red PR with no way to green it. This used to be three
hand-written copies in two workflows and a pre-commit hook, agreeing only by review, and they had
already diverged on anchoring.

Patterns are matched with `re.search`, so each one anchors itself.
"""

from __future__ import annotations

# The Bazel manifests. Two consumers, one set. For renovate-derived-files.yml this means "an input
# that invalidates MODULE.bazel.lock moved" — the module graph, or the bazel release, whose
# `lockFileVersion` and recorded extension shape the lock tracks. For the base image it means the
# manifests the image is *assembled from*: the `devcontainers_base_debian` oci.pull it sits on,
# plus the rules_oci / rules_pkg versions that lay it out.
#
# MODULE.bazel is deliberately matched whole-file rather than grepped for the pull's name. It moves
# on roughly 1 in 6 commits here and most of those cannot touch the image, so the false positives
# are real and not cheap: two base-image runners, plus a consumer build that cold-misses `cacheFrom`
# from the FROM layer down. Narrowing to one dep name would trade that for silence on a rules_oci or
# rules_pkg bump that *does* change the assembled bytes — the hole this exists to close.
#
# `.bazelversion` is the expensive direction, deliberately: the bazel version cannot enter `pkg_tar`
# layers over a pulled base, so every bazelisk bump buys a publish of a byte-identical index. The
# cheap alternative — leave it out of the re-derivation and let //.devcontainer:test_base_image_pin
# fail if the digest ever does move — leaves the recovery path needing a config change at the moment
# someone is already confused. A recurring known cost beats a rare unrecoverable one.
#
# Root-anchored: there is one Bazel module and one .bazelversion, both at the repo root, and
# bazelisk resolves upward to the root copy regardless. If a nested module ever appears, whether it
# restales the base-image pin deserves a deliberate answer rather than one inherited from a set
# written for something else.
BAZEL = (
    r"^MODULE\.bazel$",
    r"^\.bazelversion$",
)

# Everything the devcontainer base image is assembled from — the publish job's gate. Without the
# manifests above, Renovate's automerged digest bump would move the pin, skip every base job, and
# never republish, so the published image would keep the superseded Debian layers and the bump would
# reach nothing.
BASE = (
    # A plain prefix rather than a per-file allowlist. The README under here is rationale rather
    # than build input, but the image is cheap to rebuild and an allowlist would drift from the
    # directory.
    r"^meta/devcontainer-base/",
    *BAZEL,
)

# Anything that can change what this repo's own devcontainer builds. A superset of BASE by
# construction, which is what the BASE_IMAGE override needs: the load step is gated on `base` and
# the consumer build on `changed`, so a path setting `base` alone would load an image nothing then
# builds against. Including BASE is also what gates an automerged bump on a real consumer build,
# which is what the publish job requires.
CHANGED = (
    r"^\.devcontainer/",
    r"^\.github/workflows/devcontainer\.yml$",
    # This module is in the set on purpose. The sets used to live in devcontainer.yml, which the
    # workflow's own pattern covers, so an edit to them always forced a consumer build in the PR
    # that made it. Moving them here would otherwise drop that: a set edit that classifies nothing
    # still imports, so every gated step would skip and the required check would go green having
    # built nothing — and stay wrong afterwards with nothing failing. BASE needs no equivalent,
    # since editing these patterns cannot move the image's bytes.
    # //meta/scripts:test_path_classification_pattern_sets keeps this pattern here, so a deletion
    # turns red rather than quiet.
    r"^meta/scripts/path_classification_pattern_sets\.py$",
    *BASE,
)

# Python manifests Renovate can move. Whether a Python change ALSO needs a Bazel lock refresh is
# decided later, on whether requirements_lock.txt actually moved — a pyproject-only edit that
# re-resolves the same leaves MODULE.bazel.lock's pip `facts` valid. `(^|/)` rather than `^`: a
# workspace member's pyproject.toml lives at depth.
PYTHON = (
    r"(^|/)pyproject\.toml$",
    r"(^|/)uv\.lock$",
    r"(^|/)requirements_lock\.txt$",
)

# A Renovate Go bump always edits a go.mod (and go.sum alongside it), so go.mod covers every case;
# go.sum and go.work.sum are derived by tidy/sync and never the sole trigger. go.work carries the go
# directive and the workspace member list. `(^|/)` catches the per-module go.mod at any depth.
GO = (
    r"(^|/)go\.mod$",
    r"(^|/)go\.work$",
)

# Only devcontainer.json moves the feature references, and the lock is keyed by the reference
# string, so any edit here restales every entry. `^`-anchored: this repo has exactly one
# devcontainer, and a vendored copy under some subdirectory is not ours to re-resolve. The
# Dockerfile and lifecycle scripts feed the image build instead — CHANGED covers those.
DEVCONTAINER = (r"^\.devcontainer/devcontainer\.json$",)

# The name each set is selected by on the command line and in `$GITHUB_OUTPUT`.
SETS: dict[str, tuple[str, ...]] = {
    "bazel": BAZEL,
    "base": BASE,
    "changed": CHANGED,
    "python": PYTHON,
    "go": GO,
    "devcontainer": DEVCONTAINER,
}
