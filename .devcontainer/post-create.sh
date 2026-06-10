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

# pre-commit refuses to `install` when core.hooksPath is set — it can't own a
# hooks dir it doesn't control. We've observed core.hooksPath set on this repo
# to the very path git would use by default ($(git_common_dir)/hooks), making
# it a redundant no-op that only serves to block the install. Defensively
# clear it, but *only* when it's redundant: if it points somewhere else it was
# set deliberately (a custom hooks dir), so leave it and warn rather than
# silently clobbering intent. The unset is scoped with --worktree when the
# repo has per-worktree config enabled (extensions.worktreeConfig), else it
# falls back to the standard --local; either way it never touches global config.
hooks_path="$(git config --get core.hooksPath || true)"
if [ -n "$hooks_path" ]; then
  default_hooks_path="$(git rev-parse --path-format=absolute --git-common-dir)/hooks"

  # Use bash's `-ef` (same-inode) test rather than string equality: in worktree
  # devcontainers the host-absolute path in `config.worktree` (e.g.
  # /Users/jjyanchar/.dotfiles/.git/hooks) is symlinked to /host-git-common/hooks
  # by the Dockerfile + the bind mount from initialize.sh, so the two strings
  # name the SAME directory through a symlink but compare unequal as text. `-ef`
  # resolves both and tests inode equality, so the redundant case is recognized
  # whether or not the worktree-fix symlink layer is in play.
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

# Wire up the git hooks defined in .pre-commit-config.yaml. pre-commit is on
# PATH via remoteEnv (~/.local/bin), but that PATH may not be in effect during
# postCreate, so call it by full path. The source-guard hook shells out to the
# default `python3` (now ~/.venv's 3.9.6) regardless of pre-commit's own venv.
"$HOME/.local/bin/pre-commit" install

# Warm Bazel: fetches the registered Go SDK, rules_go, gazelle, etc.
bazel version

# Verify buildifier is available. Version is pinned and tracked by Renovate (in Dockerfile).
buildifier --version
