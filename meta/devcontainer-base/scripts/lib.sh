# shellcheck shell=bash
#
# Shared devcontainer host-plumbing library. Sourced by devcontainer-plumbing.sh, and by
# meta/devcontainer-base/test_plumbing.py to exercise the pure decisions directly.
#
# Deliberately does NOT set -e/-o pipefail: it is sourced into callers that own their own
# shell options. The dispatcher sets them.
#
# The `plumbing_*_is_safe` / `plumbing_*_action` functions are pure decisions, split from the
# effects they drive so they can be tested without privileged writes. When adding logic here,
# put the decision in a pure function and leave only the effect at the call site.

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

# These files are written by a host-side stub that lives in the *consuming* repo, so validate
# rather than trust: both values feed privileged commands below.
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
# the consumer's devcontainer.json binds, so a worktree's .git file resolves with no GIT_*
# overrides.
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

  # Fail loud: the consumer's postCreate runs git against the worktree right after this.
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
  # Re-validate here, not just where the value was discovered: the producer is the consuming
  # repo's host stub, and this is where the privileged `ln` happens.
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

# Apply everything the host stub presented. Idempotent, so calling it at both postCreate and
# postStart is free — which is required, because postCreate does not re-run when a container
# is reused but `devcontainer up` does re-run initializeCommand.
plumbing_apply_all() {
  local plumbing_dir="$1" workspace="$2"

  if [ -s "$plumbing_dir/host-git-common-path" ]; then
    plumbing_apply_git_common "$plumbing_dir" "$workspace" || return 1
    plumbing_apply_shared_index_config "$workspace" || return 1
  elif [ "${PLUMBING_REQUIRE_GIT_CHECKOUT:-0}" = 1 ]; then
    # Opt-in policy fork: Syndic/.dotfiles treats a non-git workspace as a bootstrap
    # failure, while this repo keeps a graceful else-branch. An env flag rather than
    # divergent copies of the script.
    plumbing_warn "no host git common dir recorded and PLUMBING_REQUIRE_GIT_CHECKOUT=1"
    return 1
  fi

  if [ -s "$plumbing_dir/host-timezone" ]; then
    plumbing_apply_timezone "$(cat "$plumbing_dir/host-timezone")" || return 1
  fi
}
