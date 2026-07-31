#!/usr/bin/env bash
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"

# Re-apply the host facts on every start: postCreate doesn't re-run when a container is
# reused, but `devcontainer up` does re-run initializeCommand and can rewrite them. Same
# base-image command as post-create.sh; see the note there.
PLUMBING_WORKSPACE="$(cd "$here/.." && pwd)" \
  PLUMBING_DIR="$here/.git-plumbing" \
  /usr/local/bin/devcontainer-plumbing post-start

# Docker creates the mount parent root-owned 0755 before any hook runs, and SSH ignores a
# ~/.ssh it considers unsafe. `install -d` fixes the directory without touching the
# read-only binds inside it.
sudo install -d -m 700 -o "$(id -u)" -g "$(id -g)" "$HOME/.ssh"

# Install host ~/.gitconfig when the Dev Containers extension didn't already copy it in
# (devcontainer CLI case). See ".devcontainer signed commits under CLI" in .claude/CLAUDE.md.
src="$here/.git-plumbing/host-gitconfig"
if [ ! -s "$HOME/.gitconfig" ] && [ -s "$src" ]; then
  cp "$src" "$HOME/.gitconfig"
fi

# The gitconfig we just copied still names a host-only path for the allowed-signers file,
# so `git verify-commit` reads our own signed commits back as untrusted. Repoint it at the
# bind mount. Keyed on the destination rather than on "did we just copy", so this also
# fires under VS Code — it bridges the gitconfig but not the file it names.
signers_dst="$HOME/.ssh/allowed_signers"
if [ -s "$signers_dst" ]; then
  git config --global gpg.ssh.allowedSignersFile "$signers_dst"
fi

# Docker Desktop bind-mounts the magic ssh-agent socket root-owned mode 660,
# so the non-root remoteUser can't connect until we re-own it.
sock=/run/host-services/ssh-auth.sock
if [ -S "$sock" ] && [ "$(stat -c '%u' "$sock")" != "$(id -u)" ]; then
  sudo chown "$(id -u):$(id -g)" "$sock"
fi
