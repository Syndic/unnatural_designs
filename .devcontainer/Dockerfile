FROM mcr.microsoft.com/devcontainers/base:debian

# renovate: datasource=github-releases depName=bazelbuild/bazelisk
ARG BAZELISK_VERSION=v1.29.0

# renovate: datasource=github-releases depName=bazelbuild/buildtools
ARG BUILDIFIER_VERSION=v8.5.1

# uv: the workspace's Python package manager. Single source of truth for the
# uv.lock + requirements_lock.txt chain consumed by rules_python's pip.parse
# and enforced by the `uv-lock-fresh` pre-commit hook. Installed first so
# later layers can layer `uv tool install ruff ty pre-commit` on top of it.
# Pinned tag bumped by Renovate's docker manager (matches the pattern used in
# ~/.dotfiles/.devcontainer/Dockerfile).
COPY --from=ghcr.io/astral-sh/uv:0.11.26 /uv /uvx /usr/local/bin/

# ruff: format + lint. Installed via `uv tool install` so the binary lives in
# /usr/local/bin and `ruff` is on PATH for the pre-commit hooks and the VS Code
# extension. UV_TOOL_DIR is forced to a world-readable system location because
# the install runs as root; the default ~/.local/share/uv would land under
# /root (mode 700) and the vscode user could not follow the bin/ symlink.
# Renovate's regex manager tracks RUFF_VERSION via the comment above the ARG.
# renovate: datasource=pypi depName=ruff
ARG RUFF_VERSION=0.15.20
# ty: static type checker. Same `uv tool install` pattern as ruff so the
# binary lands on PATH for CI, pre-commit, and the `astral-sh.ty` editor
# extension. UV_TOOL_{BIN_DIR,DIR} are shared across all uv tool installs.
# renovate: datasource=pypi depName=ty
ARG TY_VERSION=0.0.56
# pre-commit: the hook runner. Installed via `uv tool install` (not pip) so
# we have a single Python package manager in the devcontainer; the binary
# lands at /usr/local/bin/pre-commit, so post-create.sh and any contributor
# shell can invoke it directly without a $HOME/.local/bin PATH dance.
# renovate: datasource=pypi depName=pre-commit
ARG PRE_COMMIT_VERSION=4.6.0
# UV_TOOL_DIR holds the per-tool venvs; UV_PYTHON_INSTALL_DIR is where uv
# downloads its managed Python interpreters. Both must be world-readable
# because tools install as root but run as the vscode user. The Python path
# matters for *pure-Python* tools (pre-commit): the venv's `bin/python`
# symlinks into UV_PYTHON_INSTALL_DIR, and a pre-commit shebang to a path
# under root-owned /root/.local (uv's default) makes `pre-commit` fail with
# "bad interpreter: Permission denied" for vscode. ruff and ty are standalone
# binaries and don't actually invoke the symlinked Python, which is why this
# only surfaces with pre-commit.
ENV UV_TOOL_BIN_DIR=/usr/local/bin \
    UV_TOOL_DIR=/usr/local/share/uv-tools \
    UV_PYTHON_INSTALL_DIR=/usr/local/share/uv-python
RUN uv tool install --no-cache "ruff==${RUFF_VERSION}" \
    && uv tool install --no-cache "ty==${TY_VERSION}" \
    && uv tool install --no-cache "pre-commit==${PRE_COMMIT_VERSION}"

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
