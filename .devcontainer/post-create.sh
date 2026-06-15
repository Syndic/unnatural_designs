#!/usr/bin/env bash
set -euo pipefail

# Lifecycle note: postCreateCommand runs BEFORE the Dev Containers extension copies the host
# ~/.gitconfig into the container. The copy happens between postCreate and postStart, so any
# logic that reads git config — user.email, user.signingkey, gpg.* settings, etc. — will see
# an empty config here and must live in a postStartCommand script instead.

# Make the named-volume mounts writable by the non-root user. Docker attaches volumes
# root-owned on first mount, and the .cache parent of the bazel mount inherits that, so
# the chown covers .cache itself. postCreateCommand reruns on every rebuild, so this
# self-heals UID drift if remoteUser later changes (assuming the new user has sudo). If
# chown fails loudly here, recover with `docker volume rm ud-bazel-cache ud-go-cache`.
sudo chown -R "$(id -u):$(id -g)" "$HOME/.cache" "$HOME/go"

# Install golangci-lint. Version is pinned and tracked by Renovate (see renovate.json).
# renovate: datasource=github-releases depName=golangci/golangci-lint
GOLANGCI_LINT_VERSION=v2.12.2
go install "github.com/golangci/golangci-lint/v2/cmd/golangci-lint@${GOLANGCI_LINT_VERSION}"

# Install pre-commit and wire up the hooks.
pip install --user --no-warn-script-location pre-commit

# pre-commit refuses to `install` when core.hooksPath is set. Clear it when
# redundant (equals git's default); leave + warn otherwise (might be a custom
# hooks dir the user wants). Scope the unset to --worktree if available so
# global config is never touched.
hooks_path="$(git config --get core.hooksPath || true)"
if [ -n "$hooks_path" ]; then
  default_hooks_path="$(git rev-parse --path-format=absolute --git-common-dir)/hooks"

  # `-ef` (same-inode) not string-equality: the worktree-fix symlink layer
  # (initialize.sh + Dockerfile) makes the two paths name the same dir via a
  # symlink, so they compare unequal as text but resolve to the same inode.
  if [ "$hooks_path" -ef "$default_hooks_path" ]; then
    if [ "$(git config --get extensions.worktreeConfig || true)" = "true" ]; then
      git config --worktree --unset core.hooksPath
    else
      git config --unset core.hooksPath
    fi
    echo "post-create: unset redundant core.hooksPath (equalled git's default $default_hooks_path)" >&2
  else
    echo "post-create: core.hooksPath is set to a non-default path ($hooks_path) - leaving it alone" >&2
    echo "post-create: 'pre-commit install' may fail; clear it manually if that's not intended" >&2
  fi
fi

# Full path: postCreate may not see remoteEnv's PATH yet.
"$HOME/.local/bin/pre-commit" install

# Materialize the uv workspace's .venv so the ty editor extension (and any
# CLI `ty check` run) can resolve third-party imports. Without it, ty has no
# search path beyond first-party + stdlib and every non-stdlib import is
# flagged. Idempotent; subsequent rebuilds are no-ops if the lockfile is
# unchanged.
uv sync

# Warm Bazel: fetches the registered Go SDK, rules_go, gazelle, etc.
bazel version

# Verify buildifier is available. Version is pinned and tracked by Renovate (in Dockerfile).
buildifier --version
