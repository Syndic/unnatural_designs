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
- `host-gitconfig` — snapshot of the host's `~/.gitconfig`. `post-start.sh`
  installs it into the container only when `~/.gitconfig` is empty (i.e.
  the `devcontainer` CLI path; VS Code's Dev Containers extension copies
  it for itself). See ".devcontainer signed commits under CLI" in
  `.claude/CLAUDE.md`.
- `host-known-hosts` — snapshot of the host's `~/.ssh/known_hosts`.
  `post-start.sh` installs it into the container only when
  `~/.ssh/known_hosts` is empty, so `git push` from inside the CLI-
  launched container doesn't trip "Host key verification failed" on first
  contact with github.com. Same VS-Code-wins behaviour as the gitconfig
  copy.
- `host-allowed-signers` — snapshot of the file named by the host's
  `gpg.ssh.allowedSignersFile` (public keys and principal emails only).
  `post-start.sh` installs it at `~/.ssh/allowed_signers` and repoints the
  container's `gpg.ssh.allowedSignersFile` at that copy, so `git
  verify-commit` trusts our own SSH-signed commits instead of reporting
  them untrusted. The repoint also runs under VS Code, which bridges the
  gitconfig but not the file it names.

This directory exists in git (via this README) so the Dockerfile's
`COPY .devcontainer/.git-plumbing/ …` step always finds a source — buildx
errors on a COPY whose glob matches zero files, and CI's `devcontainer
build` doesn't run `initializeCommand`, so the runtime-written files are
absent there. The tracked README makes the COPY a guaranteed no-op in that
case while keeping the runtime artifacts out of the index.
