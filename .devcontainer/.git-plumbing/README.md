# Devcontainer host-plumbing artifacts

Holds host state that `../initialize.sh` reads on every `devcontainer up`, for
the shared plumbing to apply inside the container (it ships in the base image
the Dockerfile `FROM`s; its source is `meta/devcontainer-base/`).
The files are gitignored and rewritten on every `up`; this README is what keeps
the directory in git.

- `host-git-common-path` — absolute path of the host's git common directory
  (`git rev-parse --git-common-dir`). The shared plumbing recreates that same
  host-absolute path inside the container as a symlink to `/host-git-common`,
  so a worktree's `.git` file resolves natively. Empty when the workspace
  isn't a git checkout, which makes the step a no-op. See "What the plumbing
  solves" in `meta/devcontainer-base/README.md` for the why.
- `host-timezone` — IANA zone name (e.g. `America/Los_Angeles`) of the host's
  timezone. The shared plumbing points `/etc/localtime` at it so container
  timestamps match the host. Empty is fine — the container keeps its default.
- `host-gitconfig` — snapshot of the host's `~/.gitconfig`. `post-start.sh`
  installs it into the container only when `~/.gitconfig` is empty (i.e. the
  `devcontainer` CLI path; VS Code's Dev Containers extension copies it for
  itself). See "Signed commits under the devcontainer CLI" in
  `meta/devcontainer-base/README.md`.

The host's known_hosts and allowed_signers are *not* snapshotted here.
`initialize.sh` instead drops sibling symlinks — `../.host-known-hosts` and
`../.host-allowed-signers` — pointing at whatever the host actually uses, and
`devcontainer.json` binds those. `empty-allowed-signers` is the fallback target
for the second one when `gpg.ssh.allowedSignersFile` is unset or unreadable, so
the bind never dangles. See "Signed commits under the devcontainer CLI" in
`meta/devcontainer-base/README.md`.
