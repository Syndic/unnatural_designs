#!/usr/bin/env bash
set -euo pipefail

# Ensure the cache volume is writable by the non-root user.
sudo chown -R "$(id -u):$(id -g)" "$HOME/.cache/bazel" "$HOME/go" 2>/dev/null || true

# Install golangci-lint at the version pinned in .golangci.yml's toolchain (if present).
go install github.com/golangci/golangci-lint/cmd/golangci-lint@latest

# Install pre-commit and wire up the hooks.
pip install --user --no-warn-script-location pre-commit
"$HOME/.local/bin/pre-commit" install

# Warm Bazel: fetches the registered Go SDK, rules_go, gazelle, etc.
bazel version
