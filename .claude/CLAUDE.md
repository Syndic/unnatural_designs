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

`.git-plumbing/` is a tracked directory anchored by its README so the Dockerfile's `COPY` always
has a source — buildx errors on a `COPY` whose glob matches zero files, and CI's `devcontainer
build` doesn't run `initializeCommand`, so the runtime files are absent there. The shell
`[ -s ... ]` guards in the Dockerfile make that case a clean no-op (CI keeps default UTC and skips
the git path symlink).
