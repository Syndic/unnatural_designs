# Devcontainer host-plumbing artifacts

Holds host-generated artifacts that bridge host state into the devcontainer
image. The files are gitignored and written on every `devcontainer up` by
`../initialize.sh`:

- `host-git-common-path` — absolute path of the host's git common directory
  (`git rev-parse --git-common-dir`). The Dockerfile reads it at build time
  and recreates that same host-absolute path inside the image as a symlink
  to `/host-git-common`, so a worktree's `.git` file resolves natively
  in-container. See ".devcontainer worktree + timezone plumbing" in
  `.claude/CLAUDE.md` for the why.
- `host-timezone` — IANA zone name (e.g. `America/Los_Angeles`) of the
  host's timezone. The Dockerfile reads it and symlinks `/etc/localtime`,
  so container timestamps match the host.

This directory exists in git (via this README) so the Dockerfile's
`COPY .devcontainer/.git-plumbing/ …` step always finds a source — buildx
errors on a COPY whose glob matches zero files, and CI's `devcontainer
build` doesn't run `initializeCommand`, so the runtime-written files are
absent there. The tracked README makes the COPY a guaranteed no-op in that
case while keeping the runtime artifacts out of the index.
