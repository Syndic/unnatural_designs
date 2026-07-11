# Project Instructions

## Documentation and test hygiene

Documentation and tests are part of every task, not a separate follow-up step.

Before declaring any task complete:

1. **Tests** — new behaviour needs new tests; changed behaviour needs updated tests. If a method
   signature, flag, config field, or observable output changed, the relevant test files change too.

2. **Docs** — after any change to a CLI flag, environment variable, config schema, output format,
   dependency, or CI/workflow structure, grep for markdown files that reference the affected
   component and update them in the same commit.

3. **Future considerations** — if a doc item in `docs/future-considerations.md` described something
   that has now been done, update or remove that item.

## Comment style — keep it tight, trim what's oversized

Default to short, single-sentence inline comments that name the non-obvious WHY at the line they
describe. Don't write rationale-prose blocks inline; the home for architectural or cross-cutting
rationale is this CLAUDE.md (see the devcontainer plumbing section below as the model — overview
here, terse pointers at the sites).

When you touch a file whose existing comments feel oversized for the rent they pay, tightening them
as part of the change is welcome and doesn't need a separate task. Leave the load-bearing facts;
cut the prose around them. If you're unsure whether a comment is load-bearing, surface the proposed
cut before applying it.

## Reminder tags — use only the documented set

Reminder tags (`TODO`, `FIXME`, `HACK`, `NOTE`, `TEND(<task-type>)`) are a closed, greppable
vocabulary defined in `docs/reminder-tags.md`. Don't invent new ones — an ad-hoc tag like
`TRANSIENT:` or `DEFERRED:` is invisible to the per-category grep the system exists for. Pick the
closest documented tag (context/rationale → `NOTE`; correct-now-but-will-change → `TEND`); if none
fits, add the tag to `docs/reminder-tags.md` first, then use it. Watch for accidental tag shapes
too: a leading `Word:` in a comment reads like a tag even when it's just prose — reword it (e.g. to
a parenthetical) so a tag sweep doesn't trip on it.

## commit-file-via-app is a public contract

`.github/actions/commit-file-via-app/` has external consumers referencing it at `@main` — read
[its README](../.github/actions/commit-file-via-app/README.md) before changing its inputs or its
no-op-when-no-diff behavior.

## Renovate auto-commit helper (`Renovate helper` app)

`.github/workflows/renovate-derived-files.yml` regenerates the lock files Renovate can't
(Mend-hosted Renovate can't run `uv lock` or `bazel mod deps` — the `allowedUnsafeExecutions`
gate) and commits them back to the PR via a dedicated GitHub App, the `Renovate helper`.
Load-bearing facts:

- **Why an app, not `GITHUB_TOKEN`.** The commit goes through the `commit-file-via-app` action
  (see the section above), whose `createCommitOnBranch` mutation is web-flow-signed — satisfying
  branch protection — and is attributed to the app, so the push retriggers required status checks.
  A `GITHUB_TOKEN`-authored push suppresses downstream check runs; don't switch to it.
- **uv before Bazel, one commit.** `pip.parse` reads `requirements_lock.txt`, and the artifact
  hashes it resolves are recorded in `MODULE.bazel.lock`'s `facts`. So the workflow settles
  `requirements_lock.txt` (uv) before regenerating `MODULE.bazel.lock` (`bazel mod deps`), and
  commits both in one mutation — two separate workflows couldn't order the steps and would race two
  mutations on `expectedHeadOid`. A Python PR triggers the Bazel refresh only when it actually moves
  `requirements_lock.txt` from the merge base (a pyproject-only edit that re-resolves the same is a
  no-op). The Bazel refresh only rewrites the pip `facts` on a cold Bazel output base, so the
  workflow's `setup-bazel` sets only `bazelisk-cache`/`repository-cache` — adding `disk-cache` would
  make it a silent no-op.
- **App permissions.** `Contents: read & write` (commit) and `Pull requests: read & write` (to file
  and dismiss the `REQUEST_CHANGES` reviews the ratify step raises on an unresolvable bump). Set in
  the app's GitHub settings; no code.
- **`gitIgnoredAuthors` is an exact-string match.** `renovate.json` lists the helper bot's
  commit-author email so Renovate doesn't treat the auto-commit as a user edit — which would
  suppress its own follow-up rebases/bumps on that branch. Renovate matches by exact string (a
  `Set.delete`, no wildcard); the email is `<numeric-id>+<app-slug>[bot]@users.noreply.github.com`,
  and both halves change if the app is recreated.

**If the app is recreated:** wait for the next Renovate PR where the auto-commit fires, read the new
author email
(`gh api repos/Syndic/unnatural_designs/pulls/<n>/commits --jq '.[].commit.author.email'`), update
`gitIgnoredAuthors` in `renovate.json`, and re-grant the two permissions above. Until that lands
Renovate reacts to the helper's commits as user edits — visible, not load-bearing.

## .devcontainer worktree + timezone plumbing

The devcontainer is brought up by Claude agents via the `devcontainer` CLI (humans use VS Code's
Dev Containers extension). The CLI does NOT special-case git worktrees the way the extension does:
a worktree's `.git` is a file pointing at `<main-repo>/.git/worktrees/<name>`, a host-absolute path
the CLI does not mount, so in-container `git` would fail in any worktree.

The fix makes the git common dir reachable in-container at **the same absolute path it has on the
host**, so the `.git` file resolves natively (no `GIT_*` overrides) for any checkout layout — full
clone, main worktree, or a linked worktree anywhere on disk. The mechanism has three pieces; the
per-step rationale lives at each site, not here:

- `.devcontainer/initialize.sh` (wired as `initializeCommand`) — host-side, runs on every `up`.
  Drops `.devcontainer/.host-git-common` (symlink → real git common dir) and
  `.devcontainer/.git-plumbing/host-git-common-path` (absolute path of the same). Both are
  gitignored. Also writes `host-timezone` (host's IANA zone).
- `.devcontainer/devcontainer.json` — binds the symlink to a static `/host-git-common`; sets
  `workspaceFolder`/`workspaceMount` to `${localWorkspaceFolder}` so the workspace lives at its
  real host path in the container.
- `.devcontainer/Dockerfile` — reads `host-git-common-path` from the build context and recreates
  that exact host-absolute path inside the image as a symlink to `/host-git-common`; reads
  `host-timezone` and points `/etc/localtime` at it.

`.devcontainer/devcontainer-lock.json` pins the digests of the `ghcr.io/devcontainers/features/*`
features referenced from `devcontainer.json` (common-utils, git, github-cli, go, python). It is
maintained by the `devcontainer` CLI (`devcontainer features upgrade`) and bumped by Renovate's
`devcontainer` manager; nothing else writes it.

`.git-plumbing/` is a tracked directory anchored by its README so the Dockerfile's `COPY` always
has a source — buildx errors on a `COPY` whose glob matches zero files, and CI's `devcontainer
build` doesn't run `initializeCommand`, so the runtime files are absent there. The shell
`[ -s ... ]` guards in the Dockerfile make that case a clean no-op (CI keeps default UTC and skips
the git path symlink).

## .devcontainer signed commits under CLI

The host signs with **SSH** (`gpg.format = ssh`, `commit.gpgsign = true`, no explicit
`user.signingkey` — `gpg.ssh.defaultKeyCommand` shells out to `ssh-add -L`). That makes two
things load-bearing inside the container: a usable ssh-agent socket the in-container `git` can
reach, and the host's `~/.gitconfig`. Pushing the resulting signed commit then needs a third —
the host's `~/.ssh/known_hosts`, or SSH refuses the unknown github.com fingerprint. VS Code's
Dev Containers extension supplies all three automatically — it forwards the host ssh-agent
through the VS Code Server's own SSH tunnel (a per-user socket published by the server process,
*not* Docker Desktop's magic socket — that mount isn't even present in extension-launched
containers), and bridges the host gitconfig + known_hosts between `postCreate` and `postStart`.
The `devcontainer` CLI does none of that. The fix is four additive pieces:

- **SSH agent** — `devcontainer.json` binds Docker Desktop's magic socket
  `/run/host-services/ssh-auth.sock` (Desktop's documented mechanism for exposing the host's
  `ssh-agent` to any container on macOS) and sets `SSH_AUTH_SOCK` via `containerEnv`. The
  macOS launchd path is *not* used: it's unreachable inside the container and rotates across
  reboots. Trade-off: the mount is Docker-Desktop-specific and would dangle under colima /
  OrbStack / podman. A socat-based engine-agnostic relay is the known alternative; not worth
  the setup until a non-DD engine is on the table. Docker Desktop intercepts that path even
  though it isn't physically on the host; on other engines it isn't intercepted *and*
  doesn't exist, so the bind would fail at container start. `initialize.sh` checks
  `docker info` for "Docker Desktop"; if absent it `sudo touch`es a placeholder at the magic
  path so the bind succeeds (agent forwarding won't be functional there, which is fine —
  CI's smoke job just needs the container to start).
- **SSH agent socket ownership** — Docker Desktop bind-mounts the magic socket root-owned
  mode 660. The remoteUser is `vscode` (uid 1000), so it can't connect to a root-owned
  socket. `post-start.sh` `chown`s it to the current user (vscode has passwordless sudo in
  the `devcontainers/base:debian` image). Has to happen on every container start, because
  the bind-mounted socket is re-created root-owned each time. Harmless under VS Code, which
  uses its own tunneled socket and ignores the magic one entirely.
- **`~/.gitconfig`** — `initialize.sh` writes a snapshot to `.git-plumbing/host-gitconfig`.
  `post-start.sh` copies it to `$HOME/.gitconfig` *only if that file is missing or empty*.
  `postStartCommand` runs after the Dev Containers extension's own gitconfig copy, so the
  empty-check naturally lets VS Code win when it's involved; CI's `devcontainer build` never
  reaches postStart, so the file is absent there and the script is a clean no-op. Same
  lifecycle/buildx-safety story as `host-git-common-path` and `host-timezone` — gitignored,
  regenerated every `up`, anchored by the tracked `.git-plumbing/README.md`.
- **`~/.ssh/known_hosts`** — same shape as the gitconfig copy. `initialize.sh`
  snapshots the host's `~/.ssh/known_hosts` to `.git-plumbing/host-known-hosts`;
  `post-start.sh` installs it into `$HOME/.ssh/known_hosts` only if that file
  is missing or empty (creating `~/.ssh` mode 700 first). Without it, `git
  push` from inside the CLI-launched container fails with "Host key
  verification failed" on first contact with github.com — the base image's
  `~/.ssh` is empty and SSH refuses unknown fingerprints by default. VS Code
  bridges known_hosts itself, so the empty-check leaves that path alone.
  Same gitignored / regenerated-every-`up` lifecycle as the gitconfig snapshot.

`gpg.ssh.allowedSignersFile` in the copied gitconfig points to a host-only path that doesn't
exist in the container. Irrelevant: git only reads it for `git verify-commit`, not for
signing — `git log --show-signature` prints "Unable to open allowed keys file…" / "No
principal matched" and the commit is still validly signed.
