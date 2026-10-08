# The shared plumbing arrives as an image rather than as workspace scripts: this FROMs
# meta/devcontainer-base/, digest-pinned and Renovate-bumped. That directory's README is the
# canonical home for the mechanism, this three-line override shape, and why the alias is
# load-bearing — do not collapse it without reading "Consuming the image" there.
ARG BASE_IMAGE=pinned-base
FROM ghcr.io/syndic/unnatural_designs-devcontainer-base:latest@sha256:614ae9c927838c910b0ab2439079799604f013fceecb97eabe9805b27e9b5911 AS pinned-base
FROM ${BASE_IMAGE}

# uv: the workspace's Python package manager. Single source of truth for the
# uv.lock + requirements_lock.txt chain consumed by rules_python's pip.parse
# and enforced by the `uv-lock-fresh` pre-commit hook. Installed first so
# the `uv tool install` layers below can build on it.
# Pinned by tag and index digest, both moved by Renovate's docker manager; see README's
# "Image references".
COPY --from=ghcr.io/astral-sh/uv:0.12.19@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424 /uv /uvx /usr/local/bin/

# ruff: format + lint. Installed via `uv tool install` so the binary lives in
# /usr/local/bin and `ruff` is on PATH for the pre-commit hooks and the VS Code
# extension. UV_TOOL_DIR is forced to a world-readable system location because
# the install runs as root; the default ~/.local/share/uv would land under
# /root (mode 700) and the vscode user could not follow the bin/ symlink.
# Renovate's regex manager tracks RUFF_VERSION via the comment above the ARG.
# renovate: datasource=pypi depName=ruff
ARG RUFF_VERSION=0.16.9
# ty: static type checker. Same `uv tool install` pattern as ruff so the
# binary lands on PATH for CI, pre-commit, and the `astral-sh.ty` editor
# extension. UV_TOOL_{BIN_DIR,DIR} are shared across all uv tool installs.
# renovate: datasource=pypi depName=ty
ARG TY_VERSION=0.0.84
# pre-commit: the hook runner. Installed via `uv tool install` (not pip) so
# we have a single Python package manager in the devcontainer; the binary
# lands at /usr/local/bin/pre-commit, so post-create.sh and any contributor
# shell can invoke it directly without a $HOME/.local/bin PATH dance.
# renovate: datasource=pypi depName=pre-commit
ARG PRE_COMMIT_VERSION=4.6.2
# UV_TOOL_DIR holds the per-tool venvs; UV_PYTHON_INSTALL_DIR is where uv
# downloads its managed Python interpreters (both the pre-commit tool venv's
# interpreter and the interactive `python3` installed below). Both must be
# world-readable because tools install as root but run as the vscode user. The
# Python path matters for *pure-Python* tools (pre-commit, cosmic-ray,
# check-jsonschema): the venv's `bin/python` symlinks into UV_PYTHON_INSTALL_DIR,
# and a shebang to a path under root-owned /root/.local (uv's default) makes them
# fail with "bad interpreter: Permission denied" for vscode. ruff and ty are
# standalone binaries and don't actually invoke the symlinked Python.
ENV UV_TOOL_BIN_DIR=/usr/local/bin \
    UV_TOOL_DIR=/usr/local/share/uv-tools \
    UV_PYTHON_INSTALL_DIR=/usr/local/share/uv-python
RUN uv tool install --no-cache "ruff==${RUFF_VERSION}" \
    && uv tool install --no-cache "ty==${TY_VERSION}" \
    && uv tool install --no-cache "pre-commit==${PRE_COMMIT_VERSION}"

# Agent tooling rather than anything a hook or editor runs: cosmic-ray for Python mutation
# testing, check-jsonschema to validate workflows and configs against SchemaStore schemas.
# renovate: datasource=pypi depName=cosmic-ray
ARG COSMIC_RAY_VERSION=8.7.0
RUN uv tool install --no-cache "cosmic-ray==${COSMIC_RAY_VERSION}"
# renovate: datasource=pypi depName=check-jsonschema
ARG CHECK_JSONSCHEMA_VERSION=0.38.2
RUN uv tool install --no-cache "check-jsonschema==${CHECK_JSONSCHEMA_VERSION}"

# Interactive Python for the devcontainer: a prebuilt uv-managed CPython, not the
# `python` devcontainer feature (which compiles from source, ~2 min/build). uv drops
# it into UV_PYTHON_INSTALL_DIR (world-readable, above); the symlinks put `python3`/
# `python` on PATH for the smoke test, the ms-python extension, and interactive use.
# `--no-project` ignores any workspace .venv; `--managed-python` resolves the uv copy.
# A copy of //:.python-version, not a second source: the build context never COPYs the workspace,
# so this ARG cannot read that file. Renovate keeps the two level (Language toolchain SDKs) and
# //meta/scripts:check_python_version fails the build if they ever disagree.
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

# Upstream release binaries: each download is checked against a sha512 pinned per arch, which
# Renovate re-derives on a version bump. The convention is under README's "Release binaries".
# renovate: datasource=github-releases depName=bazelbuild/bazelisk
ARG BAZELISK_VERSION=v1.29.0
# renovate: datasource=github-release-attachments depName=bazelbuild/bazelisk digestVersion=v1.29.0
ARG BAZELISK_SHA512_AMD64=a76360e844fc9ce7840234557a7568beb5ab77abdce231ea5c3c61f3a8638ff879b75130b955bb6544470ed9551a5d4bfde65e6e85444046c1f149582fc872bf
# renovate: datasource=github-release-attachments depName=bazelbuild/bazelisk digestVersion=v1.29.0
ARG BAZELISK_SHA512_ARM64=da5af216952f09771ce46c0aa644eea481330adef49b6e36213ecb1698d5dcf2f25bbae9bdab670b726ffe7b0a7f6ebfec8c5ff02b8b93255a93542f57c22706
RUN ARCH="$(dpkg --print-architecture)" \
    && case "${ARCH}" in \
        amd64) SHA512="${BAZELISK_SHA512_AMD64}" ;; \
        arm64) SHA512="${BAZELISK_SHA512_ARM64}" ;; \
        *) echo "no pinned bazelisk digest for ${ARCH}" >&2; exit 1 ;; \
    esac \
    && curl -fsSL -o /tmp/bazelisk \
        "https://github.com/bazelbuild/bazelisk/releases/download/${BAZELISK_VERSION}/bazelisk-linux-${ARCH}" \
    && echo "${SHA512}  /tmp/bazelisk" | sha512sum -c - \
    && install -m 0755 /tmp/bazelisk /usr/local/bin/bazel \
    && rm /tmp/bazelisk \
    && ln -s /usr/local/bin/bazel /usr/local/bin/bazelisk

# renovate: datasource=github-releases depName=bazelbuild/buildtools
ARG BUILDIFIER_VERSION=v10.1.0
# renovate: datasource=github-release-attachments depName=bazelbuild/buildtools digestVersion=v10.1.0
ARG BUILDIFIER_SHA512_AMD64=b1d520ff6f2850a8b5f83dc72edc4d0fde55dff6f3844a569e53304fcacd25b6b89e1aa81bf47a48cc74484bf16fb377266f14032adb72c92bad20c5aceebdde
# renovate: datasource=github-release-attachments depName=bazelbuild/buildtools digestVersion=v10.1.0
ARG BUILDIFIER_SHA512_ARM64=c783ac9518a2fd5b7d52d677a005fe862b8a89f74915330e91dfd77b5ff621d489235688b17cdb2191708af91b63ab92e1fd4042b6e7bc4b6d9965b7b83bed26
RUN ARCH="$(dpkg --print-architecture)" \
    && case "${ARCH}" in \
        amd64) SHA512="${BUILDIFIER_SHA512_AMD64}" ;; \
        arm64) SHA512="${BUILDIFIER_SHA512_ARM64}" ;; \
        *) echo "no pinned buildifier digest for ${ARCH}" >&2; exit 1 ;; \
    esac \
    && curl -fsSL -o /tmp/buildifier \
        "https://github.com/bazelbuild/buildtools/releases/download/${BUILDIFIER_VERSION}/buildifier-linux-${ARCH}" \
    && echo "${SHA512}  /tmp/buildifier" | sha512sum -c - \
    && install -m 0755 /tmp/buildifier /usr/local/bin/buildifier \
    && rm /tmp/buildifier

# shellcheck from upstream rather than Debian's package, so the version is ours to pin: these
# ARGs are the repo's only shellcheck version and digest, and ci.yml reads them out of this file
# instead of using the runner image's copy. The editor extension is pointed at the binary this
# installs.
# Release assets use uname-style arch names, which dpkg's differ from.
# renovate: datasource=github-releases depName=koalaman/shellcheck
ARG SHELLCHECK_VERSION=v0.11.0
# renovate: datasource=github-release-attachments depName=koalaman/shellcheck digestVersion=v0.11.0
ARG SHELLCHECK_SHA512_AMD64=25df28923d7d89cfdb9fa8aeef762a887f2bd4ebfbcd43ae71068c60e9458d66250b0f8d6fd66b4bc03723e6cdd47983c547e3ab4f3ea2cbc4bc4028842cc7b9
# renovate: datasource=github-release-attachments depName=koalaman/shellcheck digestVersion=v0.11.0
ARG SHELLCHECK_SHA512_ARM64=de5e49175861ce567b98ec7ae0dd25f679c169d15b015bbad8f92ca22d07bb17c0fb906f7d80857e8450945f6a185d4de45e5883514e9aeb3cc155306060225e
RUN ARCH="$(dpkg --print-architecture)" \
    && case "${ARCH}" in \
        amd64) SC_ARCH=x86_64 SHA512="${SHELLCHECK_SHA512_AMD64}" ;; \
        arm64) SC_ARCH=aarch64 SHA512="${SHELLCHECK_SHA512_ARM64}" ;; \
        *) echo "no shellcheck release for ${ARCH}" >&2; exit 1 ;; \
    esac \
    && curl -fsSL -o /tmp/shellcheck.tar.xz \
        "https://github.com/koalaman/shellcheck/releases/download/${SHELLCHECK_VERSION}/shellcheck-${SHELLCHECK_VERSION}.linux.${SC_ARCH}.tar.xz" \
    && echo "${SHA512}  /tmp/shellcheck.tar.xz" | sha512sum -c - \
    && tar -xJf /tmp/shellcheck.tar.xz -C /tmp \
    && install -m 0755 "/tmp/shellcheck-${SHELLCHECK_VERSION}/shellcheck" /usr/local/bin/shellcheck \
    && rm -rf /tmp/shellcheck.tar.xz "/tmp/shellcheck-${SHELLCHECK_VERSION}"

# actionlint runs shellcheck over `run:` blocks, resolving it from PATH — the pinned copy above.
# Its asset names carry the version without the tag's leading `v`. `install` rather than
# extracting in place: the tarball's entry is owned by uid 1001, which root's tar would keep.
# renovate: datasource=github-releases depName=rhysd/actionlint
ARG ACTIONLINT_VERSION=v1.7.12
# renovate: datasource=github-release-attachments depName=rhysd/actionlint digestVersion=v1.7.12
ARG ACTIONLINT_SHA512_AMD64=c476b850a53a7777911ea29fd89e2b4caa89f22f35ffb1c11c5aae33d544a89e2b95050b491ef6dcf0572d8a0a15a01f61dca22879eaaa58a7d96f2fc0ef611f
# renovate: datasource=github-release-attachments depName=rhysd/actionlint digestVersion=v1.7.12
ARG ACTIONLINT_SHA512_ARM64=6862b29d439b62b97d3a9d95e150890ee5f5a19e615c2c47c3a3fa8367c31c6552857380130cba99d111c45cdd4c15767650466c77a6b2f8ba1f74caaf9f2e33
RUN ARCH="$(dpkg --print-architecture)" \
    && case "${ARCH}" in \
        amd64) SHA512="${ACTIONLINT_SHA512_AMD64}" ;; \
        arm64) SHA512="${ACTIONLINT_SHA512_ARM64}" ;; \
        *) echo "no pinned actionlint digest for ${ARCH}" >&2; exit 1 ;; \
    esac \
    && curl -fsSL -o /tmp/actionlint.tar.gz \
        "https://github.com/rhysd/actionlint/releases/download/${ACTIONLINT_VERSION}/actionlint_${ACTIONLINT_VERSION#v}_linux_${ARCH}.tar.gz" \
    && echo "${SHA512}  /tmp/actionlint.tar.gz" | sha512sum -c - \
    && tar -xzf /tmp/actionlint.tar.gz -C /tmp actionlint \
    && install -m 0755 /tmp/actionlint /usr/local/bin/actionlint \
    && rm /tmp/actionlint.tar.gz /tmp/actionlint

# mikefarah/yq, the Go one — not the Debian `yq` package, which is a jq wrapper with other syntax.
# renovate: datasource=github-releases depName=mikefarah/yq
ARG YQ_VERSION=v4.53.6
# renovate: datasource=github-release-attachments depName=mikefarah/yq digestVersion=v4.53.6
ARG YQ_SHA512_AMD64=508f4ee34c6d093d136ff3c1b36db4105e686c3d68d435c064448ba16e2616d528efafb2cb0a1890b7724890373a226a38b7da27e6796396ee759bed39a8c671
# renovate: datasource=github-release-attachments depName=mikefarah/yq digestVersion=v4.53.6
ARG YQ_SHA512_ARM64=d7b42b08f3cc2cf115ceb0579c143a7fccceee9e496075004bcbc34e173cfd0a4dacc0bdc6fc502c2869586b9fc949116db5fe9bef973c1a14568f4bd625edfa
RUN ARCH="$(dpkg --print-architecture)" \
    && case "${ARCH}" in \
        amd64) SHA512="${YQ_SHA512_AMD64}" ;; \
        arm64) SHA512="${YQ_SHA512_ARM64}" ;; \
        *) echo "no pinned yq digest for ${ARCH}" >&2; exit 1 ;; \
    esac \
    && curl -fsSL -o /tmp/yq \
        "https://github.com/mikefarah/yq/releases/download/${YQ_VERSION}/yq_linux_${ARCH}" \
    && echo "${SHA512}  /tmp/yq" | sha512sum -c - \
    && install -m 0755 /tmp/yq /usr/local/bin/yq \
    && rm /tmp/yq

# No host-specific layers here on purpose: the git-common-dir symlink and the host timezone are
# applied at container start by the base image's dispatcher, which keeps this image host-agnostic.
# See meta/devcontainer-base/README.md.
