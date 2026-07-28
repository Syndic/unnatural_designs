#!/usr/bin/env bash
set -euo pipefail

# devcontainer.json `initializeCommand` — runs ON THE HOST on every `devcontainer
# up`, before the container exists. It only *reads* host state (plus the two ssh
# trust files and the agent-socket placeholder it has to guarantee) and drops the
# results in .git-plumbing/; plumbing.sh applies them container-side.
#
# Everything it writes is gitignored and regenerated every `up`, so the values can
# never go stale and concurrent worktrees don't collide. Full rationale lives in
# ".devcontainer worktree + timezone plumbing" in .claude/CLAUDE.md.
#
# The `initialize_*` functions are pure, split out so test_plumbing.py can source this
# file and exercise them without touching host state.

# /etc/localtime's symlink target -> IANA zone name, or empty if it isn't a zoneinfo link.
initialize_zone_from_link() {
  case "$1" in
    *zoneinfo/*) printf '%s' "${1##*zoneinfo/}" ;;
    *) printf '' ;;
  esac
}

# Reject absolute paths and traversal: plumbing.sh builds /usr/share/zoneinfo/$tz from this
# and feeds it to a privileged `ln`, so a hostile or broken /etc/localtime target must not
# be able to point that elsewhere. Empty is a safe answer — the apply step then no-ops.
initialize_sanitize_tz() {
  case "$1" in
    /* | *..*) printf '' ;;
    *) printf '%s' "$1" ;;
  esac
}

# devcontainer.json's `mounts` can only interpolate ${localEnv:}, so the allowed-signers bind
# names a fixed path. Anything else configured would be silently ignored in-container, so this
# is checked rather than followed. Unset is fine — nothing to disagree with.
initialize_signers_ok() {
  local configured="$1" bound="$2"
  [ -z "$configured" ] || [ "$configured" = "$bound" ]
}

# Sourcing (the tests) stops here; only direct execution runs the imperative body below.
[ "${BASH_SOURCE[0]}" = "${0}" ] || return 0

here="$(cd "$(dirname "$0")" && pwd)"        # the .devcontainer dir (host abs)
workspace="$(cd "$here/.." && pwd)"          # repo/worktree root (host abs)
link="$here/.host-git-common"
pathfile="$here/.git-plumbing/host-git-common-path"
tzfile="$here/.git-plumbing/host-timezone"
gitconfigfile="$here/.git-plumbing/host-gitconfig"

# The .git-plumbing dir is tracked (via its README), so it normally exists
# already; mkdir -p covers stray cases like a manual deletion without
# changing behavior.
mkdir -p "$(dirname "$pathfile")"

cd "$workspace"

if common="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"; then
  # ln -sfn: replace any existing symlink in place (don't nest a new link inside
  # an old one) so re-runs after the main repo moves point at the new location.
  ln -sfn "$common" "$link"
  printf '%s\n' "$common" >"$pathfile"
else
  # Not a git checkout (shouldn't happen for this repo, but stay safe): make the
  # mount source a real-but-empty dir so Docker doesn't auto-create a stray path,
  # and leave the path file empty so plumbing.sh's symlink step no-ops. git then
  # falls back to normal discovery (which no-ops), and post-create.sh's guarded
  # `pre-commit install` skips.
  rm -rf "$link"
  mkdir -p "$link"
  : >"$pathfile"
fi

# Host timezone — discover the IANA zone name (e.g. "America/Los_Angeles") and
# persist it so plumbing.sh can apply it in-container. Without this, the
# container defaults to Etc/UTC and timestamps drift hours off the host.
#
# Two host shapes:
#   - /etc/localtime is a symlink into the zoneinfo db (macOS, most modern
#     Linux). Strip everything up to and including `zoneinfo/` to get the zone.
#   - /etc/timezone exists as a plain text file (Debian/Ubuntu, some others).
# An empty result is fine — plumbing.sh guards on `[ -s ]` and falls back to the
# container's default zone.
tz=""
if target="$(readlink /etc/localtime 2>/dev/null)"; then
  tz="$(initialize_zone_from_link "$target")"
fi
if [ -z "$tz" ] && [ -r /etc/timezone ]; then
  tz="$(tr -d '[:space:]' </etc/timezone)"
fi
printf '%s\n' "$(initialize_sanitize_tz "$tz")" >"$tzfile"

# Snapshot host ~/.gitconfig for post-start.sh to install when the Dev
# Containers extension hasn't already done so. Empty file if absent.
if [ -r "$HOME/.gitconfig" ]; then
  cp "$HOME/.gitconfig" "$gitconfigfile"
else
  : >"$gitconfigfile"
fi

# Assert before creating anything, so a host that trips this doesn't get a stray empty file
# in its real ~/.ssh on the way to a hard failure.
signers="$(git config --type=path --get gpg.ssh.allowedSignersFile 2>/dev/null || true)"
if ! initialize_signers_ok "$signers" "$HOME/.ssh/allowed_signers"; then
  echo "initialize: gpg.ssh.allowedSignersFile is $signers, but the devcontainer binds" >&2
  echo "initialize: $HOME/.ssh/allowed_signers - move the file or update the setting." >&2
  exit 1
fi

# known_hosts and allowed_signers are pure read-only trust data, so devcontainer.json binds
# them straight in rather than snapshotting them. `--mount` errors on a missing source, so
# guarantee both exist — the one place initialize.sh *creates* host files rather than only
# reading them. `: >>` appends nothing, leaving any real file untouched.
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
: >>"$HOME/.ssh/known_hosts"
: >>"$HOME/.ssh/allowed_signers"

# TEND(migration): drop with the .gitignore entries once every checkout has run an `up` on
# or after the bind-mount switch. Stops a stale checkout keeping a copy of the user's
# known_hosts lying around untracked.
rm -f "$here/.git-plumbing/host-known-hosts" "$here/.git-plumbing/host-allowed-signers"

# Pre-create the magic ssh-agent socket placeholder on hosts where Docker
# Desktop isn't intercepting it (CI runners, plain Docker on Linux). Docker
# Desktop auto-forwards the host ssh-agent at /run/host-services/ssh-auth.sock
# even though that path isn't physically present on the host; elsewhere the
# bind declared in devcontainer.json fails before the container starts.
# Placeholder makes the bind succeed; SSH forwarding won't be functional
# there but the container can start (CI smoke jobs don't sign commits).
docker_info="$(docker info 2>/dev/null || true)"
if ! printf '%s' "$docker_info" | grep -q "Docker Desktop"; then
  if [ ! -e /run/host-services/ssh-auth.sock ]; then
    sudo mkdir -p /run/host-services
    sudo touch /run/host-services/ssh-auth.sock
  fi
fi
