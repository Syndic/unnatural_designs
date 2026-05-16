#!/usr/bin/env bash
set -euo pipefail

# Ensure the cache volume is writable by the non-root user. Docker creates the parent
# of a named-volume mount (here, $HOME/.cache, parent of the bazel mount) as root-owned,
# which blocks later writes like `go install` populating $HOME/.cache/go-build — so the
# chown has to cover .cache itself, not just .cache/bazel.
sudo chown -R "$(id -u):$(id -g)" "$HOME/.cache" "$HOME/go" 2>/dev/null || true

# Install golangci-lint. Version is pinned and tracked by Renovate (see renovate.json).
# renovate: datasource=github-releases depName=golangci/golangci-lint
GOLANGCI_LINT_VERSION=v2.12.2
go install "github.com/golangci/golangci-lint/v2/cmd/golangci-lint@${GOLANGCI_LINT_VERSION}"

# Install pre-commit and wire up the hooks.
pip install --user --no-warn-script-location pre-commit
"$HOME/.local/bin/pre-commit" install

# Warm Bazel: fetches the registered Go SDK, rules_go, gazelle, etc.
bazel version
