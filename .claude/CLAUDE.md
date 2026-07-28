# Project Instructions

## Run all tooling in the devcontainer

Every build, test, linter, formatter, code generator, language tool, and `git commit` runs via
`devcontainer exec --workspace-folder . <cmd>`. The host is only for editing files, read-side `git`
(`status`/`log`/`diff`/`rev-parse`/`branch`), and `gh`.

Spin the container up on the first task that could run in it, independent of any cost assessment —
**treat spin-up as free.** This is a standing user preference, not a trade-off to weigh: the user
wants the container up. Bring it up once, reuse it for everything after:

```
devcontainer up --workspace-folder .          # once, at first tool need
devcontainer exec --workspace-folder . <cmd>  # every tool invocation after
```

A missing host tool is a signal to use the container, never to provision it on the host: no
`pip`/`brew`/`go install`, no `uvx`/`npx` to dodge the container (host-version drift, and it won't
satisfy the `language: system` pre-commit hooks anyway). Commits especially — hooks resolve tools
from PATH and signing needs the bridged SSH agent; see the ".devcontainer signed commits" section.

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

## Superseding CI runs

Every workflow except `renovate-derived-files.yml` carries the same block, and the shape of the
group key is the load-bearing part:

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.run_id }}
  cancel-in-progress: true
```

- **PRs group by number**, so a fast-follow push cancels the previous run. The motivating case is a
  Renovate PR: the helper's derived-files commit lands ~30s after Renovate's push, so the first
  devcontainer build spent ~5 of its ~5.5 minutes building a tree — bumped manifest, un-regenerated
  lock — that never merges. Required status checks are evaluated against the PR's head SHA, so
  cancelling a superseded SHA's run never leaves a requirement unmet.
- **Everything else keys on `run_id`**, which is unique per run, so non-PR runs are always alone in
  their group: nothing cancels them and — the subtler half — nothing *queues* behind them either.
  A shared non-PR group would have made a push to main wait on an in-progress weekly security scan
  (`cancel-in-progress: false` semantics), and a third run in that group would be dropped outright.
  Keeping them ungrouped matters because those runs produce artifacts nothing else will: the GHCR
  devcontainer build cache (`push: filter`), the Codecov upload, and the main-branch security
  baseline.

`renovate-derived-files.yml` is the deliberate exception; the reason is at that file's own
`concurrency` note, since it is a property of that workflow rather than of the convention.

## Renovate auto-commit helper (`Renovate helper` app)

`.github/workflows/renovate-derived-files.yml` regenerates the derived files Renovate can't
(Mend-hosted Renovate can't run `uv lock`, `bazel mod deps`, or `go mod tidy`/`go work sync` —
the `allowedUnsafeExecutions` gate) and commits them back to the PR via a dedicated GitHub App,
the `Renovate helper`. Load-bearing facts:

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
- **`.bazelversion` invalidates the lock too.** `MODULE.bazel.lock`'s `lockFileVersion` (and the
  shape of its recorded extensions) tracks the bazel release, so a Renovate bazel bump leaves the
  committed lock stale. CI hides it — `--lockfile_mode=update` rewrites in memory and stays green —
  but the `bazel mod tidy` pre-commit hook rewrites it on disk, so the staleness surfaces as a
  blocked local commit on any `go.mod`/`MODULE.bazel` change. The workflow therefore triggers on
  `.bazelversion` and folds it into the same `bazel` classification as `MODULE.bazel`: one output,
  one `bazel mod deps` refresh. bazelisk reads the checked-out `.bazelversion`, so the regenerated
  lock is in the bumped version's format.
- **Go tidy/sync rides the same commit.** Renovate's `go get` bumps `go.mod`/`go.sum` but never
  runs `go mod tidy` (opt-in) or `go work sync` (Renovate does it only when vendoring, which this
  repo doesn't) — so the indirect block and `go.work.sum` are left stale. The workflow runs
  `go mod tidy` in each `go.work` member, then `go work sync`, and adds the touched go.mod/go.sum
  plus `go.work.sum` to the single commit. Unlike uv→Bazel, Go is *independent* of the other two
  (no ordering constraint); it shares the job purely so a grouped Go+Python+Bazel PR settles in one
  `expectedHeadOid` mutation instead of racing. The tidy/sync target lists come from
  `meta/scripts/go_derived_files.py` (`--what modules|files`), which reads `go.work`'s `use`
  directives — so a module added to the workspace is picked up with no workflow edit. Unlike the
  uv path, there's no "conflict" review: a bump `go mod tidy` can't settle just fails the job.
  MODULE.bazel.lock is *not* in this set — gazelle's `go_deps` extension is reproducible and absent
  from the lockfile, and `use_repo` tracks only direct imports (unchanged by a version bump).
- **Devcontainer feature lock rides it too.** `devcontainer upgrade` reruns when
  `devcontainer.json` moves. Like Go, it is *independent* of the uv→Bazel ordering and shares the
  job only so a grouped PR settles in one `expectedHeadOid` mutation. Needs no Docker (OCI metadata
  only), so the cost is an npm install of the CLI. The lock's derived-file contract, and why the
  references must be full semver and carry no digests, live in the devcontainer plumbing section.
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
  Reads host state and drops it in `.git-plumbing/`: `host-git-common-path` (absolute path of the
  git common dir), `host-timezone` (host's IANA zone), `host-gitconfig`. Also drops
  `.devcontainer/.host-git-common`, a symlink to the real common dir. All gitignored.
- `.devcontainer/devcontainer.json` — binds the symlink to a static `/host-git-common`; sets
  `workspaceFolder`/`workspaceMount` to `${localWorkspaceFolder}` so the workspace lives at its
  real host path in the container.
- `.devcontainer/plumbing.sh` — applies those facts at container start, invoked from the top of
  both `post-create.sh` and `post-start.sh`. Recreates the host-absolute git path as a symlink to
  `/host-git-common`, points `/etc/localtime` at the host zone, and writes the shared-index config
  below.

**The application is deliberately at runtime, not baked into the image.** Nothing forces it to
build time — the workspace isn't even mounted then — and baking the two per-host facts is the only
thing that would stop the image being shared with `Syndic/.dotfiles`. Two consequences to keep in
mind: the timezone lands *after* container start, so a process launched before `postCreate` (the VS
Code server, notably) keeps UTC timestamps until restarted; and the `/etc` writes live in the
container's writable layer, so any future prebuild mechanism would re-bake host facts and undo
this. `onCreateCommand` is the wrong home for the same reason — it runs during prebuilds.

Two ordering facts the code depends on. `plumbing.sh` must run at the *top* of `post-create.sh`,
because everything below it runs git against the worktree. And it must re-run at `postStart`:
`devcontainer up` re-runs `initializeCommand` and can rewrite the path file even for an existing
container, but `postCreate` does not re-run. Every step is idempotent so the double application is
free — including the `/etc/environment` `TZ=` line, which is rewritten rather than appended.

`.devcontainer/devcontainer-lock.json` pins a resolved version + digest for each
`ghcr.io/devcontainers/features/*` feature referenced from `devcontainer.json`. It is a **derived
file** in exactly the sense `MODULE.bazel.lock` is: `renovate-derived-files.yml` regenerates it with
`devcontainer upgrade` whenever a PR moves `devcontainer.json`, and commits it in that workflow's
single commit, and `devcontainer.yml` fails the build if the committed copy doesn't match what
`upgrade --dry-run` would write. That check is in CI rather than pre-commit — unlike `bazel mod tidy`
or `uv lock`, the devcontainer CLI is a host/runner tool and is deliberately absent from the image,
so a `language: system` hook running *inside* the container could not invoke it.

To re-resolve the lock by hand: `devcontainer upgrade --workspace-folder .` (`--dry-run` prints
instead of writing). Note that under exact pins `upgrade` can no longer *advance* anything — it only
makes the lock agree with the references. Moving a feature version means editing the tag in
`devcontainer.json`; `devcontainer outdated` will report Wanted/Latest equal to the pin, not the
newest release.

Two conventions make that work, and both are load-bearing:

- **Feature references are pinned to full semver** (`features/go:1.3.4`), not the floating major
  tags (`:1`) the devcontainer templates emit. Renovate's `devcontainer` manager tracks features as
  `docker` deps, so an exact tag is a version it can bump — and that bump is the event the derived
  file regenerates from. Under a floating `:1` there is nothing for Renovate to propose, no PR, and
  therefore no trigger; the lock then drifts with nothing to notice. It did: it sat two features
  behind (common-utils 2.5.8→2.5.9, git 1.3.5→1.3.8) for as long as nobody thought to look.
- **Digests are not pinned in the reference.** The devcontainer spec supports `@sha256:` on `image`
  but not on features. Renovate's `pinDigests` output — `features/go:1.3.4@sha256:…` — makes the
  CLI **silently drop the feature**: no error, it simply stops being installed. (The tagless
  `features/go@sha256:…` form does resolve, but carries no version for Renovate to compare, so it
  would be pinned forever and invisible.) `renovate.json` therefore sets `pinDigests: false` for the
  `devcontainer` manager. Digest pinning is not lost — the lock records
  `resolved: …@sha256:…` per feature, which is where the digest belongs.

Note the lock is keyed by the *reference string*, so a version bump restales every entry rather than
just the one field — which is why the regeneration is a full `devcontainer upgrade` and not a patch.

Python is deliberately **not** a devcontainer feature: that feature compiles CPython from source
(~2 min per build). The Dockerfile installs a prebuilt uv-managed interpreter instead — `ARG
PYTHON_VERSION` (Renovate-tracked via the Dockerfile custom manager, `depName=python`, so it stays
in the "Language toolchain SDKs" group alongside the `MODULE.bazel` and `setup-python` pins) — and
symlinks `python3`/`python` onto PATH. The CI `Devcontainer` job caches the built image in GHCR
(`imageName`/`cacheFrom`, `push: filter` seeds it on pushes to main), so the feature layers are
reused across runs rather than rebuilt cold; this needs the workflow's `packages: write`.

`.git-plumbing/` is tracked via its README; the files inside are gitignored and rewritten on every
`up`. `plumbing.sh` guards each step on `[ -s ... ]`, so a missing or empty file is a clean no-op
(no git checkout → no symlink and no shared-index config; no zone → the container keeps its
default).

## .devcontainer shared git index

A consequence of the shared-common-dir design above: host git and in-container git read and write
the **same index file**, but they sit in different stat domains — the bind mount reports different
uid/gid, inode, ctime, and sub-second mtime for the same files. Under git's default
`core.checkStat`, an index written on one side reads as "everything modified" on the other without
any content comparison (`diff-index` flags every tracked file), so checkout-type operations —
rebase, merge, branch switch — refuse with "your local changes would be overwritten" on a clean
tree. Plain commits never hit this (no checkout involved), which is why the breakage only surfaced
on an in-container rebase.

`plumbing.sh` therefore sets `core.checkstat = minimal` and `core.trustctime = false` in the
repo-local config. That config lives in the common dir, so one write covers both sides and every
worktree — which is also why it can be written from the container side at all. `minimal` reduces the stat check to whole-second mtime + file size — the two fields the
bind mount preserves — making the index portable in both directions; `trustctime = false` guards
against ctime-only divergence from metadata changes (chmod/chown) one side doesn't observe. Known
trade-off: a same-size edit landing in the same second as the last index refresh can evade stat
detection; git's racy-index protection (entries at least as new as the index itself get
content-checked) covers the realistic window. CI's smoke job asserts both settings landed — proof
that `post-create.sh` reached `plumbing.sh`. That `devcontainers/ci` runs `initializeCommand` at
all (plain `devcontainer build` doesn't) is pinned separately, by asserting the host-written
artifacts in `.git-plumbing/` are present.

## .devcontainer signed commits under CLI

The host signs with **SSH** (`gpg.format = ssh`, `commit.gpgsign = true`, no explicit
`user.signingkey` — `gpg.ssh.defaultKeyCommand` shells out to `ssh-add -L`). That makes two
things load-bearing inside the container: a usable ssh-agent socket the in-container `git` can
reach, and the host's `~/.gitconfig`. Pushing the resulting signed commit then needs a third —
the host's `~/.ssh/known_hosts`, or SSH refuses the unknown github.com fingerprint. *Verifying*
it locally needs a fourth — the allowed-signers file named by `gpg.ssh.allowedSignersFile`, or
`git verify-commit` reports a good-but-untrusted signature (`%G?` → `U`). VS Code's Dev
Containers extension supplies the first three automatically — it forwards the host ssh-agent
through the VS Code Server's own SSH tunnel (a per-user socket published by the server process,
*not* Docker Desktop's magic socket — that mount isn't even present in extension-launched
containers), and bridges the host gitconfig + known_hosts between `postCreate` and `postStart`.
It does not bridge the allowed-signers file. The `devcontainer` CLI does none of it. The fix is
five additive pieces — two bind mounts and three scripted steps:

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
  empty-check naturally lets VS Code win when it's involved. It stays a snapshot rather than
  becoming a bind mount like the two below, because the container *rewrites* it (see the
  allowed-signers repoint): a read-only mount would break the repoint and a read-write one
  would leak container edits back to the host.
- **`~/.ssh/known_hosts`** — a read-only bind mount from `${localEnv:HOME}`, declared in
  `devcontainer.json`. Without it, `git push` from inside the CLI-launched container fails
  with "Host key verification failed" on first contact with github.com — the base image's
  `~/.ssh` is empty and SSH refuses unknown fingerprints by default. VS Code bridges
  known_hosts itself; the mount simply wins there, harmlessly. Cost of `readonly`: ssh can't
  record newly-accepted host keys, so it warns and continues.
- **`~/.ssh/allowed_signers`** — the trust set `git verify-commit` checks a signature against,
  same read-only bind. Git reads it only for verification, never for signing, which is why
  commits signed fine before this piece existed while `git log --show-signature` printed
  "Unable to open allowed keys file…" / "No principal matched". Contents are public keys and
  principal emails — no secret material.

  Two things follow from these being *mounts*. Docker creates the mount parent root-owned
  0755 before any hook runs, and SSH ignores a `~/.ssh` it considers unsafe, so
  `post-start.sh` `install -d`s the directory back to the user at mode 700 — without touching
  the binds inside it. And `mounts` can only interpolate `${localEnv:}`, so the path is fixed
  at `~/.ssh/allowed_signers` where the old snapshot honoured whatever
  `gpg.ssh.allowedSignersFile` named. `initialize.sh` keeps that setting authoritative by
  *asserting* the two agree and failing loud otherwise, rather than silently binding the
  wrong file. Both files are also `touch`ed on the host if absent, because a bind mount with
  a missing source aborts container start — the one place `initialize.sh` writes host state
  rather than only reading it.

  The repoint the mounts don't remove: the copied gitconfig still points
  `gpg.ssh.allowedSignersFile` at the *host-absolute* path (`/Users/...`), which doesn't
  exist in the container, so `post-start.sh` rewrites it (`git config --global`) to the
  mounted path whenever that file is non-empty. Keying the rewrite on the *destination*
  rather than on "did we just copy" makes it fire under VS Code too — the extension bridges
  the gitconfig, stale path and all, but not the file it names — and honours an
  allowed_signers a user provisioned some other way.
