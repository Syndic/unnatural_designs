#!/usr/bin/env bash
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"

# Re-apply the host facts on every start: postCreate doesn't re-run when a container is
# reused, but `devcontainer up` does re-run initializeCommand and can rewrite them. Same
# base-image command as post-create.sh; see the note there. The `post-start` verb also
# selects the image's signing bridge, which runs at this phase only.
PLUMBING_WORKSPACE="$(cd "$here/.." && pwd)" \
  PLUMBING_DIR="$here/.git-plumbing" \
  /usr/local/bin/devcontainer-plumbing post-start

