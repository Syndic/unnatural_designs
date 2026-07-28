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
COPY --from=ghcr.io/astral-sh/uv:0.12.0 /uv /uvx /usr/local/bin/

# ruff: format + lint. Installed via `uv tool install` so the binary lives in
# /usr/local/bin and `ruff` is on PATH for the pre-commit hooks and the VS Code
# extension. UV_TOOL_DIR is forced to a world-readable system location because
# the install runs as root; the default ~/.local/share/uv would land under
# /root (mode 700) and the vscode user could not follow the bin/ symlink.
# Renovate's regex manager tracks RUFF_VERSION via the comment above the ARG.
# renovate: datasource=pypi depName=ruff
ARG RUFF_VERSION=0.16.0
# ty: static type checker. Same `uv tool install` pattern as ruff so the
# binary lands on PATH for CI, pre-commit, and the `astral-sh.ty` editor
# extension. UV_TOOL_{BIN_DIR,DIR} are shared across all uv tool installs.
# renovate: datasource=pypi depName=ty
ARG TY_VERSION=0.0.65
# pre-commit: the hook runner. Installed via `uv tool install` (not pip) so
# we have a single Python package manager in the devcontainer; the binary
# lands at /usr/local/bin/pre-commit, so post-create.sh and any contributor
# shell can invoke it directly without a $HOME/.local/bin PATH dance.
# renovate: datasource=pypi depName=pre-commit
ARG PRE_COMMIT_VERSION=4.6.1
# UV_TOOL_DIR holds the per-tool venvs; UV_PYTHON_INSTALL_DIR is where uv
# downloads its managed Python interpreters (both the pre-commit tool venv's
# interpreter and the interactive `python3` installed below). Both must be
# world-readable because tools install as root but run as the vscode user. The
# Python path matters for *pure-Python* tools (pre-commit): the venv's
# `bin/python` symlinks into UV_PYTHON_INSTALL_DIR, and a pre-commit shebang to
# a path under root-owned /root/.local (uv's default) makes `pre-commit` fail
# with "bad interpreter: Permission denied" for vscode. ruff and ty are
# standalone binaries and don't actually invoke the symlinked Python, which is
# why this only surfaces with pre-commit.
ENV UV_TOOL_BIN_DIR=/usr/local/bin \
    UV_TOOL_DIR=/usr/local/share/uv-tools \
    UV_PYTHON_INSTALL_DIR=/usr/local/share/uv-python
RUN uv tool install --no-cache "ruff==${RUFF_VERSION}" \
    && uv tool install --no-cache "ty==${TY_VERSION}" \
    && uv tool install --no-cache "pre-commit==${PRE_COMMIT_VERSION}"

# Interactive Python for the devcontainer: a prebuilt uv-managed CPython, not the
# `python` devcontainer feature (which compiles from source, ~2 min/build). uv drops
# it into UV_PYTHON_INSTALL_DIR (world-readable, above); the symlinks put `python3`/
# `python` on PATH for the smoke test, the ms-python extension, and interactive use.
# `--no-project` ignores any workspace .venv; `--managed-python` resolves the uv copy.
# Kept level with the setup-python/MODULE.bazel pins by Renovate (Language toolchain SDKs).
# renovate: datasource=python-version depName=python
ARG PYTHON_VERSION=3.14
RUN uv python install "${PYTHON_VERSION}" \
    && ln -sfn "$(uv python find --no-project --managed-python "${PYTHON_VERSION}")" /usr/local/bin/python3 \
    && ln -sfn /usr/local/bin/python3 /usr/local/bin/python

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

# No host-specific layers here on purpose: the git-common-dir symlink and the host timezone
# are applied at container start by .devcontainer/plumbing.sh, which keeps this image
# host-agnostic. See ".devcontainer worktree + timezone plumbing" in .claude/CLAUDE.md.
