#!/usr/bin/env bash
set -euo pipefail

# Make the named-volume mounts writable by the non-root user: Docker attaches them (and
# the .cache parent of the bazel mount) root-owned, blocking writes like `go install`
# populating $HOME/.cache/go-build — so the chown covers .cache itself. This only takes
# effect on first mount; if remoteUser later changes, the volumes keep their old UID and
# this fails silently (|| true). Recover: `docker volume rm ud-bazel-cache ud-go-cache`.
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
