#!/usr/bin/env bash
set -euo pipefail

# devcontainer.json `initializeCommand` — runs ON THE HOST, before the image is
# built and the container created, on every `devcontainer up`. Its job: make the
# repo's git metadata reachable inside the container at THE SAME ABSOLUTE PATH it
# has on the host, for ANY checkout layout (full clone, the main worktree, or a
# linked worktree living anywhere on disk).
#
# Why this exists. When the `devcontainer` CLI opens a git *worktree*, the
# worktree's `.git` is a FILE reading `gitdir: <main-repo>/.git/worktrees/<name>`
# — a host-absolute path outside the workspace. That path isn't mounted, so every
# in-container git command fails. (VS Code's Dev Containers extension special-
# cases this; the CLI does not.)
#
# The constraint that shapes the mechanism: devcontainer.json's `mounts`,
# `runArgs`, and `build.args` are resolved at config-parse time and can only
# interpolate `${localEnv:VAR}` / `${localWorkspaceFolder}` — NOT a value an
# initializeCommand computes (a child process can't set the CLI's env). So the
# freshly-discovered absolute common-dir path can't be named as a mount target
# directly. We bridge it WITHOUT any GIT_* override and WITHOUT a system-wide env
# var by splitting the work between a static bind mount and the image build:
#
#   1. Drop a symlink at a fixed, workspace-relative path
#      (.devcontainer/.host-git-common) pointing at the real common dir. Docker
#      follows the symlink host-side when it binds it, so devcontainer.json can
#      name a STATIC mount source ("${localWorkspaceFolder}/.devcontainer/
#      .host-git-common") that resolves to wherever the main repo actually is,
#      and bind it to a static container path (/host-git-common).
#   2. Persist the absolute common-dir path to
#      .git-plumbing/host-git-common-path. It lands in the build context
#      (context: "..") so the Dockerfile can read it and recreate that exact
#      host-absolute path inside the image as a symlink to /host-git-common.
#      With that in place the worktree's `.git` file resolves natively — git
#      reads its real contents and follows the host-absolute pointer, no
#      GIT_DIR/GIT_COMMON_DIR/GIT_WORK_TREE needed.
#
#      The path file lives inside a tracked .git-plumbing/ dir (anchored by a
#      committed README.md) rather than as a bare gitignored sibling. Reason:
#      buildx errors on a COPY whose source glob matches zero files (the
#      classic .pat[h] optional-COPY trick works on the legacy builder but
#      not buildx), so the Dockerfile COPYs the *directory* — which always
#      exists — and treats the file inside as optional via a shell
#      `[ -s ... ]` test. CI's `devcontainer build` doesn't run
#      initializeCommand, so the path file is genuinely absent there; the
#      tracked README makes the COPY a guaranteed no-op in that case.
#
# Both runtime artifacts (symlink + path file) are gitignored and regenerated
# on every `up`, so nothing the host tracks is touched and the values can
# never go stale. The scheme works for several worktrees concurrently: each
# carries its own symlink/path file and bakes its own host-absolute symlink
# into its own image, binding its own /host-git-common — no cross-container
# collision.

here="$(cd "$(dirname "$0")" && pwd)"        # the .devcontainer dir (host abs)
workspace="$(cd "$here/.." && pwd)"          # repo/worktree root (host abs)
link="$here/.host-git-common"
pathfile="$here/.git-plumbing/host-git-common-path"
tzfile="$here/.git-plumbing/host-timezone"
gitconfigfile="$here/.git-plumbing/host-gitconfig"
knownhostsfile="$here/.git-plumbing/host-known-hosts"

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
  # and leave the path file empty so the Dockerfile's symlink step no-ops. git
  # then falls back to normal discovery (which no-ops), and post-create.sh's
  # guarded `pre-commit install` skips.
  rm -rf "$link"
  mkdir -p "$link"
  : >"$pathfile"
fi

# Host timezone — discover the IANA zone name (e.g. "America/Los_Angeles") and
# persist it so the Dockerfile can apply it to the image. Without this, the
# container defaults to Etc/UTC and timestamps in molecule/playbook output drift
# 7-8h off the host. See CLAUDE.md "Host timezone plumbing".
#
# Two host shapes:
#   - /etc/localtime is a symlink into the zoneinfo db (macOS, most modern
#     Linux). Strip everything up to and including `zoneinfo/` to get the zone.
#   - /etc/timezone exists as a plain text file (Debian/Ubuntu, some others).
# An empty result is fine — the Dockerfile guards on `[ -s ]` and falls back to
# the image's default zone.
tz=""
if target="$(readlink /etc/localtime 2>/dev/null)"; then
  case "$target" in
    *zoneinfo/*) tz="${target##*zoneinfo/}" ;;
  esac
fi
if [ -z "$tz" ] && [ -r /etc/timezone ]; then
  tz="$(tr -d '[:space:]' </etc/timezone)"
fi
# Reject path traversal or absolute paths defensively — the Dockerfile uses the
# value to build /usr/share/zoneinfo/$tz, so a hostile or broken /etc/localtime
# target shouldn't be able to point that elsewhere. The Dockerfile additionally
# checks the resolved zoneinfo file exists before applying.
case "$tz" in
  /* | *..*) tz="" ;;
esac
printf '%s\n' "$tz" >"$tzfile"

# Snapshot host ~/.gitconfig for post-start.sh to install when the Dev
# Containers extension hasn't already done so. Empty file if absent.
if [ -r "$HOME/.gitconfig" ]; then
  cp "$HOME/.gitconfig" "$gitconfigfile"
else
  : >"$gitconfigfile"
fi

# Snapshot host ~/.ssh/known_hosts the same way. Without it, `git push` from
# inside a CLI-launched container fails with "Host key verification failed"
# the first time it talks to github.com — the base image's $HOME/.ssh is
# empty and SSH refuses unknown fingerprints by default. The host's
# known_hosts is the right trust set to carry (same flavor as the gitconfig
# — both are the user's existing trust state, neither secret). Empty file
# if absent so the `[ -s ... ]` guard in post-start.sh stays a clean no-op.
if [ -r "$HOME/.ssh/known_hosts" ]; then
  cp "$HOME/.ssh/known_hosts" "$knownhostsfile"
else
  : >"$knownhostsfile"
fi

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
