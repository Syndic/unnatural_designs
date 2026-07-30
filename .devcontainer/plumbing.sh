#!/usr/bin/env bash
set -euo pipefail

# Apply the host facts initialize.sh dropped in .git-plumbing/ to the running container.
# Called at the top of both post-create.sh and post-start.sh; every step is idempotent.
# See ".devcontainer worktree + timezone plumbing" in .claude/CLAUDE.md.
#
# The `plumbing_*` functions are pure decisions, split out from the effects they drive so
# test_plumbing.py can source this file and exercise them without privileged writes.

# ── decisions (unit-tested) ───────────────────────────────────────────────────────────────

# What to do with the recorded host git-common path, given the workspace mount root.
# Echoes: skip | refresh | create | conflict
plumbing_git_common_action() {
  local p="$1" workspace="$2"

  # A full clone records <workspace>/.git, which the bind mount already provides; only
  # paths OUTSIDE the mount need bridging.
  case "$p/" in
    "$workspace"/*)
      echo skip
      return 0
      ;;
  esac

  if [ -L "$p" ]; then
    echo refresh # repoint: the main repo may have moved
  elif [ -e "$p" ]; then
    echo conflict # a real file or dir — never clobber it
  else
    echo create
  fi
}

# These files are written by a host-side stub that Phase 2 moves into a *different* repo, so
# validate rather than trust: both values feed privileged commands below.
plumbing_zone_is_safe() {
  case "$1" in
    "" | /* | *..*) return 1 ;;
    *) return 0 ;;
  esac
}

plumbing_git_path_is_safe() {
  case "$1" in
    *..*) return 1 ;;
    /*) return 0 ;;
    *) return 1 ;;
  esac
}

# New /etc/environment content: every line except TZ=, then exactly one TZ=. Rewriting
# rather than appending is what keeps this idempotent across repeated container starts.
plumbing_tz_environment() {
  local tz="$1" current="${2:-/etc/environment}" rc=0
  if [ -e "$current" ]; then
    grep -v '^[[:space:]]*TZ=' "$current" || rc=$?
    # rc 1 is "no lines survived the filter", which is fine. Anything else is a read
    # failure, and the caller overwrites /etc/environment with this output — so refuse
    # rather than hand back a file collapsed to a single TZ= line.
    [ "$rc" -le 1 ] || return 1
  fi
  printf 'TZ=%s\n' "$tz"
}

# ── effects ───────────────────────────────────────────────────────────────────────────────

plumbing_warn() { echo "plumbing: $*" >&2; }

# These write host-absolute paths and /etc, both root-owned. Tolerate already being root so
# the same code works where sudo isn't installed.
plumbing_sudo() {
  if [ "$(id -u)" = 0 ]; then "$@"; else sudo "$@"; fi
}

# Recreate the host's git common dir at its host-absolute path, pointing at the static path
# devcontainer.json binds, so a worktree's .git file resolves with no GIT_* overrides.
plumbing_apply_git_common() {
  local plumbing_dir="$1" workspace="$2" p action
  p="$(cat "$plumbing_dir/host-git-common-path")"

  if ! plumbing_git_path_is_safe "$p"; then
    plumbing_warn "refusing to bridge an unsafe git common path: $p"
    return 1
  fi

  action="$(plumbing_git_common_action "$p" "$workspace")"

  case "$action" in
    skip) ;;
    conflict)
      # Not fatal on its own — the -d check below decides — but never silent, or a
      # container running against some other .git looks like magic.
      plumbing_warn "$p already exists and is not a symlink; leaving it alone"
      ;;
    refresh) plumbing_sudo ln -sfn /host-git-common "$p" ;;
    create)
      plumbing_sudo mkdir -p "$(dirname "$p")"
      plumbing_sudo ln -sfn /host-git-common "$p"
      ;;
  esac

  # Fail loud: everything post-create.sh does afterwards runs git against the worktree.
  if [ ! -d "$p" ]; then
    plumbing_warn "git common dir did not resolve: $p (action=$action)"
    return 1
  fi
}

# Host and container share one index but see different stat metadata for the same files;
# restrict git's stat checks to the fields the bind mount preserves. Repo-local, so the
# write lands in the common dir and covers both sides and every worktree.
plumbing_apply_shared_index_config() {
  git -C "$1" config core.checkstat minimal
  git -C "$1" config core.trustctime false
}

# Point the container at the host's IANA zone so timestamps don't drift against the host.
plumbing_apply_timezone() {
  local tz="$1" tmp
  # Re-validate here, not just where the value was discovered: after Phase 2 the producer
  # is a different repo's stub and this is where the privileged `ln` happens.
  if ! plumbing_zone_is_safe "$tz"; then
    plumbing_warn "refusing to apply an unsafe zone name: $tz"
    return 0
  fi
  if [ ! -e "/usr/share/zoneinfo/$tz" ]; then
    plumbing_warn "unknown zone '$tz'; keeping the container default"
    return 0
  fi

  # Build the replacement first — piping sudo tee into the file being read would truncate
  # it at open.
  tmp="$(mktemp)"
  if ! plumbing_tz_environment "$tz" /etc/environment >"$tmp"; then
    plumbing_warn "could not read /etc/environment; leaving the timezone alone"
    rm -f "$tmp"
    return 0
  fi

  # Cosmetic step: warn and carry on rather than aborting container creation.
  if ! {
    plumbing_sudo ln -sfn "/usr/share/zoneinfo/$tz" /etc/localtime &&
      printf '%s\n' "$tz" | plumbing_sudo tee /etc/timezone >/dev/null &&
      plumbing_sudo install -m 0644 -o root -g root "$tmp" /etc/environment
  }; then
    plumbing_warn "could not apply host timezone '$tz'; keeping the container default"
  fi
  rm -f "$tmp"
}

plumbing_main() {
  local phase="${1:?usage: plumbing.sh <post-create|post-start>}"
  local here workspace plumbing_dir
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  workspace="$(cd "$here/.." && pwd)"
  plumbing_dir="${PLUMBING_DIR:-$here/.git-plumbing}"

  # An empty path file means initialize.sh found no git checkout; git falls back to normal
  # discovery and the shared-index setting has nowhere to land.
  if [ -s "$plumbing_dir/host-git-common-path" ]; then
    plumbing_apply_git_common "$plumbing_dir" "$workspace"
    plumbing_apply_shared_index_config "$workspace"
  fi

  if [ -s "$plumbing_dir/host-timezone" ]; then
    plumbing_apply_timezone "$(cat "$plumbing_dir/host-timezone")"
  fi

  # Stamp what ran: every other postStart side effect is environment-dependent, so this is
  # what CI's smoke test can assert against.
  plumbing_sudo install -d -m 0755 /run/devcontainer-plumbing
  plumbing_sudo touch "/run/devcontainer-plumbing/$phase.stamp"
}

# Only run when executed; sourcing (the tests) just loads the functions.
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  plumbing_main "$@"
fi
