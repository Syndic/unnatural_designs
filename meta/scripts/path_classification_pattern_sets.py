"""The named pattern sets every site in this repo classifies paths against.

One definition each, composed by set union, so no site restates another's patterns. Consumers
name a set: `classify_changed_paths.py` turns a diff into `name=true|false` step outputs for
devcontainer.yml, renovate-derived-files.yml and commit-file-via-app-selftest.yml, and
`base_image_pin_hook.py` gates the `base-image-pin` pre-commit hook on `BASE`.

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

# Everything the publish job's gate has to see: what the base image is assembled from, plus the
# patterns deciding whether the gate fires at all. Without the manifests above, Renovate's
# automerged digest bump would move the pin, skip every base job, and never republish, so the
# published image would keep the superseded Debian layers and the bump would reach nothing.
BASE = (
    # A plain prefix rather than a per-file allowlist. The README under here is rationale rather
    # than build input, but the image is cheap to rebuild and an allowlist would drift from the
    # directory.
    r"^meta/devcontainer-base/",
    # This module. A check's effective domain is part of its logic, so an edit here is an edit to
    # every check reading these patterns — the publish gate included, which would otherwise stand
    # down on the one commit able to classify the image's own sources out of this set. `CHANGED`
    # inherits it through the splat rather than restating it. The cost is a publish of a
    # byte-identical index on any commit touching this file: the trade `.bazelversion` above
    # already takes, for the same reason it is worth taking.
    r"^meta/scripts/path_classification_pattern_sets\.py$",
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
    # This module reaches here through `*BASE`, which carries it for the whole-repo reason stated
    # there. //meta/scripts:test_path_classification_pattern_sets holds the membership, so losing
    # it turns red rather than quiet.
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

# Everything `Action self-test`'s verdict depends on: the action it exercises, the workflow that
# decides what exercising means, and this module, which decides whether it runs at all.
#
# Unlike every other set here this one is not about a derived file — it gates a required status
# check, whose workflow therefore carries no trigger-level `paths:` filter (see .claude/CLAUDE.md
# "A required check cannot be filtered at the trigger"). The classification moved in here so the
# gate is a step inside the job that reports the check rather than a filter GitHub applies first.
COMMIT_FILE_VIA_APP = (
    # A plain prefix: action.yml, the README that states the contract, and anything added beside
    # them are all inputs to what the self-test asserts.
    r"^\.github/actions/commit-file-via-app/",
    r"^\.github/workflows/commit-file-via-app-selftest\.yml$",
    # This module. A check's effective domain is part of its logic: dropping a pattern leaves the
    # action untouched but changes the check's answer, so a PR that would have failed now passes,
    # the exercise having skipped rather than run. An edit here is therefore a change to this
    # check, and has to be treated as such.
    r"^meta/scripts/path_classification_pattern_sets\.py$",
)

# The name each set is selected by on the command line and in `$GITHUB_OUTPUT`.
SETS: dict[str, tuple[str, ...]] = {
    "bazel": BAZEL,
    "base": BASE,
    "changed": CHANGED,
    "python": PYTHON,
    "go": GO,
    "devcontainer": DEVCONTAINER,
    "commit_file_via_app": COMMIT_FILE_VIA_APP,
}
