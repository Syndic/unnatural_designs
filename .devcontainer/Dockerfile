FROM mcr.microsoft.com/devcontainers/base:debian

# renovate: datasource=github-releases depName=bazelbuild/bazelisk
ARG BAZELISK_VERSION=v1.29.0

# renovate: datasource=github-releases depName=bazelbuild/buildtools
ARG BUILDIFIER_VERSION=v8.5.1

# uv: the workspace's Python package manager. Single source of truth for the
# uv.lock + requirements_lock.txt chain consumed by rules_python's pip.parse
# and enforced by the `uv-lock-fresh` pre-commit hook. Installed first so
# later PRs can layer `uv tool install ruff ty pre-commit` on top of it.
# Pinned tag bumped by Renovate's docker manager (matches the pattern used in
# ~/.dotfiles/.devcontainer/Dockerfile).
COPY --from=ghcr.io/astral-sh/uv:0.9.30 /uv /uvx /usr/local/bin/

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        ripgrep \
        unzip \
        zip \
    && rm -rf /var/lib/apt/lists/*

RUN ARCH="$(dpkg --print-architecture)" \
    && curl -fsSL -o /usr/local/bin/bazel \
        "https://github.com/bazelbuild/bazelisk/releases/download/${BAZELISK_VERSION}/bazelisk-linux-${ARCH}" \
    && chmod 0755 /usr/local/bin/bazel \
    && ln -s /usr/local/bin/bazel /usr/local/bin/bazelisk

RUN ARCH="$(dpkg --print-architecture)" \
    && curl -fsSL -o /usr/local/bin/buildifier \
        "https://github.com/bazelbuild/buildtools/releases/download/${BUILDIFIER_VERSION}/buildifier-linux-${ARCH}" \
    && chmod 0755 /usr/local/bin/buildifier

# Make a git worktree's metadata resolve in-container at the SAME absolute path
# it has on the host, and apply the host's IANA timezone. See ".devcontainer
# worktree + timezone plumbing" in .claude/CLAUDE.md for the full rationale.
#
# .devcontainer/initialize.sh runs on the host before the build and persists
# two artifacts into .git-plumbing/: `host-git-common-path` (the abs path of
# the host's git common dir) and `host-timezone` (the host's IANA zone). Here
# we recreate the host-absolute git path inside the image as a symlink to
# /host-git-common (the static path devcontainer.json binds at runtime), so
# the worktree's .git file resolves natively with no GIT_* overrides, and we
# point /etc/localtime at the host zone.
#
# COPY the *directory* (anchored by a tracked README) rather than the files
# directly: buildx errors on a COPY whose source glob matches zero files. CI's
# `devcontainer build` never runs initializeCommand, so the runtime files are
# absent there; the `[ -s ... ]` guards keep that case a clean no-op (and CI
# keeps the image's default UTC). Kept last so the layers above stay cached
# when the per-worktree path changes.
COPY .devcontainer/.git-plumbing/ /tmp/git-plumbing/
RUN if [ -s /tmp/git-plumbing/host-git-common-path ]; then \
        p="$(cat /tmp/git-plumbing/host-git-common-path)"; \
        mkdir -p "$(dirname "$p")"; \
        ln -sfn /host-git-common "$p"; \
    fi; \
    if [ -s /tmp/git-plumbing/host-timezone ]; then \
        tz="$(cat /tmp/git-plumbing/host-timezone)"; \
        if [ -e "/usr/share/zoneinfo/$tz" ]; then \
            ln -sfn "/usr/share/zoneinfo/$tz" /etc/localtime; \
            echo "$tz" > /etc/timezone; \
            echo "TZ=$tz" >> /etc/environment; \
        fi; \
    fi; \
    rm -rf /tmp/git-plumbing
