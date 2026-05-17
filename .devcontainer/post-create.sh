#!/usr/bin/env bash
set -euo pipefail

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
"$HOME/.local/bin/pre-commit" install

# Warm Bazel: fetches the registered Go SDK, rules_go, gazelle, etc.
bazel version

# Wire up SSH-based commit signing using the host's forwarded ssh-agent.
#
# VS Code copies the host ~/.gitconfig into the container, but its
# `user.signingkey` typically points at a host filesystem path that does not
# exist here. Override it to the literal-key form (`key::ssh-ed25519 AAAA...`),
# pulled from the forwarded agent so no key material lives in the repo. Also
# write a container-local allowed_signers so `git log --show-signature` etc.
# can verify in addition to sign.
configure_ssh_signing() {
  if [[ -z "${SSH_AUTH_SOCK:-}" ]] || ! ssh-add -L >/dev/null 2>&1; then
    echo "WARN: no ssh-agent forwarded into the container; signed commits will fail." >&2
    echo "      Ensure the host ssh-agent is running with the signing key loaded and" >&2
    echo "      that VS Code's remote.SSH.enableAgentForwarding is on." >&2
    return 0
  fi

  local email key_line
  email="$(git config --global user.email || true)"
  # Prefer the agent key whose comment matches user.email; fall back to the
  # first key if no match.
  key_line=""
  if [[ -n "$email" ]]; then
    key_line="$(ssh-add -L | awk -v e="$email" '$NF == e {print; exit}')"
  fi
  if [[ -z "$key_line" ]]; then
    key_line="$(ssh-add -L | head -n1)"
    echo "WARN: no forwarded key with comment matching '$email'; using first agent key." >&2
  fi

  git config --global gpg.format ssh
  git config --global commit.gpgsign true
  git config --global user.signingkey "key::${key_line}"

  mkdir -p "$HOME/.ssh"
  chmod 700 "$HOME/.ssh"
  printf '%s %s\n' "${email:-signer}" "$key_line" > "$HOME/.ssh/allowed_signers"
  chmod 600 "$HOME/.ssh/allowed_signers"
  git config --global gpg.ssh.allowedSignersFile "$HOME/.ssh/allowed_signers"
}
configure_ssh_signing
