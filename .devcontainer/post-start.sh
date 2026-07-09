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

# Copy a host snapshot into ~/.ssh, skipping when the destination already has
# content (VS Code wins) or the snapshot is absent (`devcontainer build`).
# ~/.ssh must be mode 700 or SSH ignores it; the files themselves at 644 are fine.
install_ssh_snapshot() {
  local src="$1" dst="$2"
  if [ -s "$src" ] && [ ! -s "$dst" ]; then
    mkdir -p "$HOME/.ssh"
    chmod 700 "$HOME/.ssh"
    cp "$src" "$dst"
    chmod 644 "$dst"
  fi
}

# Host ~/.ssh/known_hosts. Lets `git push` from inside the CLI-launched
# container succeed first try; without it the user hits "Host key verification
# failed" because the base image has no known_hosts and SSH refuses unknown
# fingerprints by default. VS Code's Dev Containers extension already bridges
# known_hosts when it's involved.
install_ssh_snapshot "$here/.git-plumbing/host-known-hosts" "$HOME/.ssh/known_hosts"

# Host allowed-signers file, plus the gitconfig rewrite that makes it reachable:
# the copied gitconfig's `gpg.ssh.allowedSignersFile` still names a host-only
# path, so `git verify-commit` reads our own signed commits back as untrusted.
# Repoint it at the installed copy. Unlike the snapshots above this also has to
# fire under VS Code, which bridges the gitconfig (stale path and all) but not
# the file it names. Keyed on the destination rather than on "did we just copy",
# so a user-provisioned allowed_signers is honoured too.
signers_dst="$HOME/.ssh/allowed_signers"
install_ssh_snapshot "$here/.git-plumbing/host-allowed-signers" "$signers_dst"
if [ -s "$signers_dst" ]; then
  git config --global gpg.ssh.allowedSignersFile "$signers_dst"
fi

# Docker Desktop bind-mounts the magic ssh-agent socket root-owned mode 660,
# so the non-root remoteUser can't connect until we re-own it.
sock=/run/host-services/ssh-auth.sock
if [ -S "$sock" ] && [ "$(stat -c '%u' "$sock")" != "$(id -u)" ]; then
  sudo chown "$(id -u):$(id -g)" "$sock"
fi
