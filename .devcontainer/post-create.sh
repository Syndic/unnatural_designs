#!/usr/bin/env bash
set -euo pipefail

# Ensure the cache volume is writable by the non-root user. Docker creates the parent
# of a named-volume mount (here, $HOME/.cache, parent of the bazel mount) as root-owned,
# which blocks later writes like `go install` populating $HOME/.cache/go-build — so the
# chown has to cover .cache itself, not just .cache/bazel.
sudo chown -R "$(id -u):$(id -g)" "$HOME/.cache" "$HOME/go" 2>/dev/null || true

# Install golangci-lint at the version pinned in .golangci.yml's toolchain (if present).
go install github.com/golangci/golangci-lint/cmd/golangci-lint@latest

# Install pre-commit and wire up the hooks.
pip install --user --no-warn-script-location pre-commit
"$HOME/.local/bin/pre-commit" install

# Warm Bazel: fetches the registered Go SDK, rules_go, gazelle, etc.
bazel version
