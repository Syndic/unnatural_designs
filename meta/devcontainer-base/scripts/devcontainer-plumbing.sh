#!/usr/bin/env bash
set -euo pipefail

# Shared devcontainer host-plumbing entry point. Installed in the base image as
# /usr/local/bin/devcontainer-plumbing; consuming repos call it from the top of their
# postCreate and postStart hooks and then do their own repo-specific work.
#
# Usage: devcontainer-plumbing <post-create|post-start>
#
# Inputs (env, both optional):
#   PLUMBING_DIR                  where the host stub dropped its files.
#                                 Default: $PWD/.devcontainer/.git-plumbing
#   PLUMBING_WORKSPACE            the workspace mount root. Default: $PWD
#   PLUMBING_REQUIRE_GIT_CHECKOUT 1 to fail when no git common dir was recorded
#
# The defaults assume the cwd is the workspace folder, which is what the devcontainer CLI
# guarantees for lifecycle commands. Callers that know their own location should pass the
# values explicitly rather than rely on that — this script lives outside the workspace, so it
# cannot derive them from $0.
#
# Adding a step here reaches every consumer on its next base-image digest bump, with no edit
# on their side. That property is the reason this is a dispatcher and not just a library.

usage() {
  echo "usage: devcontainer-plumbing <post-create|post-start>" >&2
  exit 2
}

[ "$#" -eq 1 ] || usage
phase="$1"
case "$phase" in
  post-create | post-start) ;;
  *) usage ;;
esac

# Find the library beside this script (the in-repo layout) or at its installed path (the
# image layout, where this file is copied into /usr/local/bin and the library is not). Two
# candidates rather than a symlink so the image needs no RUN layer — see the Dockerfile.
lib=""
for candidate in \
  "$(cd "$(dirname "$0")" && pwd)/lib.sh" \
  "/usr/local/share/devcontainer-plumbing/lib.sh"; do
  if [ -r "$candidate" ]; then
    lib="$candidate"
    break
  fi
done
if [ -z "$lib" ]; then
  echo "devcontainer-plumbing: cannot locate lib.sh" >&2
  exit 1
fi
# shellcheck source=meta/devcontainer-base/scripts/lib.sh
. "$lib"

workspace="${PLUMBING_WORKSPACE:-$PWD}"
plumbing_dir="${PLUMBING_DIR:-$workspace/.devcontainer/.git-plumbing}"

plumbing_apply_all "$plumbing_dir" "$workspace"

# Stamp what ran: every other postStart side effect is environment-dependent, so this is what
# a consumer's smoke test can assert against. /run is tmpfs, so the stamp says "ran since this
# container was created", not "ran on this boot".
plumbing_sudo install -d -m 0755 /run/devcontainer-plumbing
plumbing_sudo touch "/run/devcontainer-plumbing/$phase.stamp"
