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

# Docker Desktop bind-mounts the magic ssh-agent socket root-owned mode 660,
# so the non-root remoteUser can't connect until we re-own it.
sock=/run/host-services/ssh-auth.sock
if [ -S "$sock" ] && [ "$(stat -c '%u' "$sock")" != "$(id -u)" ]; then
  sudo chown "$(id -u):$(id -g)" "$sock"
fi
