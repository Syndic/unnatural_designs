#!/usr/bin/env bash
set -euo pipefail

# Apply the host facts initialize.sh dropped in .git-plumbing/ to the running container.
# Called at the top of both post-create.sh and post-start.sh; every step is idempotent.
# See ".devcontainer worktree + timezone plumbing" in .claude/CLAUDE.md.

phase="${1:?usage: plumbing.sh <post-create|post-start>}"
here="$(cd "$(dirname "$0")" && pwd)"
workspace="$(cd "$here/.." && pwd)"
plumbing="$here/.git-plumbing"

warn() { echo "plumbing: $*" >&2; }

# These steps write host-absolute paths and /etc, both root-owned. Tolerate already being
# root so the same code works where sudo isn't installed.
plumbing_sudo() {
  if [ "$(id -u)" = 0 ]; then "$@"; else sudo "$@"; fi
}

# Recreate the host's git common dir at its host-absolute path, pointing at the static
# path devcontainer.json binds, so a worktree's .git file resolves with no GIT_* overrides.
apply_git_common() {
  local p
  p="$(cat "$plumbing/host-git-common-path")"

  # A full clone records <workspace>/.git, which the bind mount already provides; only
  # paths OUTSIDE the mount need bridging. `ln -sfn` onto a real directory doesn't
  # replace it — it would nest a stray link inside the host's own .git.
  case "$p/" in
    "$workspace"/*) return 0 ;;
  esac

  if [ -L "$p" ]; then
    plumbing_sudo ln -sfn /host-git-common "$p" # refresh: the main repo may have moved
  elif [ ! -e "$p" ]; then
    plumbing_sudo mkdir -p "$(dirname "$p")"
    plumbing_sudo ln -sfn /host-git-common "$p"
  fi

  # Fail loud: everything post-create.sh does afterwards runs git against the worktree.
  [ -d "$p" ] || {
    warn "git common dir did not resolve: $p"
    exit 1
  }
}

# Host and container share one index but see different stat metadata for the same files;
# restrict git's stat checks to the fields the bind mount preserves. Repo-local, so the
# write lands in the common dir and covers both sides and every worktree.
apply_shared_index_config() {
  git -C "$workspace" config core.checkstat minimal
  git -C "$workspace" config core.trustctime false
}

# Point the container at the host's IANA zone so timestamps don't drift against the host.
apply_timezone() {
  local tz tmp
  tz="$(cat "$plumbing/host-timezone")"
  if [ ! -e "/usr/share/zoneinfo/$tz" ]; then
    warn "unknown zone '$tz'; keeping the container default"
    return 0
  fi

  # Rewrite /etc/environment rather than append: this runs on every container start, and
  # the file is shared with the CLI's own containerEnv lines. Build the replacement first
  # — piping sudo tee into the file being read would truncate it at open.
  tmp="$(mktemp)"
  {
    grep -v '^[[:space:]]*TZ=' /etc/environment 2>/dev/null || true
    printf 'TZ=%s\n' "$tz"
  } >"$tmp"

  # Cosmetic step: warn and carry on rather than aborting container creation.
  if ! {
    plumbing_sudo ln -sfn "/usr/share/zoneinfo/$tz" /etc/localtime &&
      printf '%s\n' "$tz" | plumbing_sudo tee /etc/timezone >/dev/null &&
      plumbing_sudo install -m 0644 -o root -g root "$tmp" /etc/environment
  }; then
    warn "could not apply host timezone '$tz'; keeping the container default"
  fi
  rm -f "$tmp"
}

# An empty path file means initialize.sh found no git checkout; git falls back to normal
# discovery and the shared-index setting has nowhere to land.
if [ -s "$plumbing/host-git-common-path" ]; then
  apply_git_common
  apply_shared_index_config
fi

if [ -s "$plumbing/host-timezone" ]; then
  apply_timezone
fi

# Stamp what ran: every other postStart side effect is environment-dependent, so this is
# what CI's smoke test can assert against.
plumbing_sudo install -d -m 0755 /run/devcontainer-plumbing
plumbing_sudo touch "/run/devcontainer-plumbing/$phase.stamp"
