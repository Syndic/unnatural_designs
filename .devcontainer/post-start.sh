#!/usr/bin/env bash
set -euo pipefail

# Install host ~/.gitconfig when the Dev Containers extension didn't already
# copy it in (devcontainer CLI case). See ".devcontainer signed commits under
# CLI" in .claude/CLAUDE.md.

here="$(cd "$(dirname "$0")" && pwd)"
src="$here/.git-plumbing/host-gitconfig"

if [ ! -s "$HOME/.gitconfig" ] && [ -s "$src" ]; then
  cp "$src" "$HOME/.gitconfig"
fi

# Install host ~/.ssh/known_hosts the same way. Lets `git push` from inside
# the CLI-launched container succeed first try; without it the user hits
# "Host key verification failed" because the base image has no known_hosts
# and SSH refuses unknown fingerprints by default. VS Code's Dev Containers
# extension already bridges known_hosts when it's involved, so the empty-
# check leaves that path alone.
known_src="$here/.git-plumbing/host-known-hosts"
known_dst="$HOME/.ssh/known_hosts"
if [ ! -s "$known_dst" ] && [ -s "$known_src" ]; then
  # ~/.ssh must be mode 700 or SSH ignores it; known_hosts at 644 is fine.
  mkdir -p "$HOME/.ssh"
  chmod 700 "$HOME/.ssh"
  cp "$known_src" "$known_dst"
  chmod 644 "$known_dst"
fi

# Docker Desktop bind-mounts the magic ssh-agent socket root-owned mode 660,
# so the non-root remoteUser can't connect until we re-own it.
sock=/run/host-services/ssh-auth.sock
if [ -S "$sock" ] && [ "$(stat -c '%u' "$sock")" != "$(id -u)" ]; then
  sudo chown "$(id -u):$(id -g)" "$sock"
fi
