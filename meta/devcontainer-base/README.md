# Shared devcontainer base image

Builds `ghcr.io/syndic/unnatural_designs-devcontainer-base`, which carries the container-side
devcontainer host-plumbing that Syndic repos would otherwise hand-port between each other
(this repo and [Syndic/.dotfiles](https://github.com/Syndic/.dotfiles) had already done that
round trip once). Consumers `FROM` it, digest-pinned, and layer their own toolchain on top.

This file is the canonical home for *why* the plumbing exists and what it guarantees, for every
consumer. Consumer repos keep only what is theirs: their host stub, their Dockerfile, and the
repo-local facts in their own CLAUDE.md.

## What the plumbing solves

**Worktree git resolution.** A devcontainer is brought up either by VS Code's Dev Containers
extension or by the `devcontainer` CLI (which is what Claude agents use). The CLI does NOT
special-case git worktrees the way the extension does: a worktree's `.git` is a file pointing at
`<main-repo>/.git/worktrees/<name>`, a host-absolute path the CLI does not mount, so in-container
`git` would fail in any worktree.

The fix makes the git common dir reachable in-container at **the same absolute path it has on the
host**, so the `.git` file resolves natively — no `GIT_*` overrides — for any checkout layout: a
full clone, the main worktree, or a linked worktree anywhere on disk. It has three pieces, two of
them per-consumer:

- The consumer's `initialize.sh` (wired as `initializeCommand`) — host-side, runs on every `up`.
  Reads host state and drops it in `.git-plumbing/`: `host-git-common-path` (absolute path of the
  git common dir), `host-timezone` (host's IANA zone), `host-gitconfig`. Also drops
  `.host-git-common`, a symlink to the real common dir. All gitignored, rewritten every `up`, so
  they cannot go stale and concurrent worktrees don't collide.
- The consumer's `devcontainer.json` — binds that symlink to a static `/host-git-common`, and sets
  `workspaceFolder`/`workspaceMount` to `${localWorkspaceFolder}` so the workspace lives at its
  real host path in the container.
- **This image** — recreates the host-absolute git path as a symlink to `/host-git-common`, points
  `/etc/localtime` at the host zone, and writes the shared-index config below.

Each apply step guards on `[ -s ... ]`, so a missing or empty file is a clean no-op: no git
checkout means no symlink and no shared-index config, no zone means the container keeps its
default.

**The host timezone.** Without it the container defaults to `Etc/UTC` and every timestamp drifts
hours off the host.

**A shared git index across two stat domains.** A consequence of the shared-common-dir design:
host git and in-container git read and write the **same index file**, but the bind mount reports
different uid/gid, inode, ctime, and sub-second mtime for the same files. Under git's default
`core.checkStat`, an index written on one side reads as "everything modified" on the other without
any content comparison (`diff-index` flags every tracked file), so checkout-type operations —
rebase, merge, branch switch — refuse with "your local changes would be overwritten" on a clean
tree. Plain commits never hit this (no checkout involved), which is why the breakage first surfaced
on an in-container rebase.

The library therefore sets `core.checkstat = minimal` and `core.trustctime = false` in the
repo-local config. That config lives in the common dir, so one write covers both sides and every
worktree — which is also why it can be written from the container side at all. `minimal` reduces
the stat check to whole-second mtime + file size, the two fields the bind mount preserves, making
the index portable in both directions; `trustctime = false` guards against ctime-only divergence
from metadata changes (chmod/chown) one side doesn't observe. Known trade-off: a same-size edit
landing in the same second as the last index refresh can evade stat detection; git's racy-index
protection (entries at least as new as the index itself get content-checked) covers the realistic
window.

## Why the application is at runtime, not baked

Nothing forces it to build time — the workspace isn't even mounted then — and baking the two
per-host facts is the only thing that would stop the image being shared at all. Three consequences
to keep in mind:

- The timezone lands *after* container start, so a process launched before `postCreate` (the VS
  Code server, notably) keeps UTC timestamps until restarted.
- The `/etc` writes live in the container's writable layer, so any future prebuild mechanism would
  re-bake host facts and undo this. `onCreateCommand` is the wrong home for the same reason — it
  runs during prebuilds.
- The shared-index config is written by in-container `git` against a bind-mounted repo, so on any
  engine where uid mapping doesn't line up, git's dubious-ownership check would abort `postCreate`
  — a failure mode that didn't exist while this ran host-side. Docker Desktop and `devcontainers/ci`
  both map correctly (CI asserts it); rootless-Docker and colima are unsupported.

## Interface

```
devcontainer-plumbing <post-create|post-start>
```

Installed at `/usr/local/bin/devcontainer-plumbing`, with `lib.sh` at
`/usr/local/share/devcontainer-plumbing/lib.sh`. Consuming repos call the dispatcher from the
top of their `postCreateCommand` and `postStartCommand` hooks, then do their own work.

The dispatcher — rather than a library each consumer composes itself — is what retires
hand-porting: a *new* shared step ships in the image and reaches every consumer on its next
digest bump with no edit on their side. A library alone would put a hook edit in every repo
for every new step, which is the problem this replaces one level up.

| env var | default | meaning |
| --- | --- | --- |
| `PLUMBING_DIR` | `$PWD/.devcontainer/.git-plumbing` | where the host stub dropped its files |
| `PLUMBING_WORKSPACE` | `$PWD` | the workspace mount root |
| `PLUMBING_REQUIRE_GIT_CHECKOUT` | `0` | `1` to fail when no git common dir was recorded |

The defaults assume the cwd is the workspace folder, which the devcontainer CLI guarantees for
lifecycle commands. This script lives outside the workspace, so it cannot derive them from
`$0` — callers that know their own location should pass them explicitly instead of relying on
cwd.

`PLUMBING_REQUIRE_GIT_CHECKOUT` exists so the one genuine policy difference between the repos
stays *shared code*: .dotfiles treats a non-git workspace as a bootstrap failure, this repo
keeps a graceful else-branch. An env flag, not two divergent copies.

Two ordering facts the callers must honour. The dispatcher runs at the **top** of `post-create.sh`,
because everything below it runs git against the worktree. And it re-runs at `postStart`:
`devcontainer up` re-runs `initializeCommand` and can rewrite the path file even for an existing
container, but `postCreate` does not re-run. Every step is idempotent, so the double application is
free — including the `/etc/environment` `TZ=` line, which is rewritten rather than appended.

### What the host side still owns

Each consumer keeps its own `initialize.sh`. Its job is to *present* host state in the shape
this library consumes — reads dropped into `.git-plumbing/`, plus symlinks at fixed
workspace-relative names for the host paths only it can discover. It does not constrain where
the host keeps anything. That stub is small and stable, so it stays hand-copied; keeping it
out of the image also means shared code never runs elevated on a developer's host.

## Consuming the image

A consumer's Dockerfile `FROM`s the published image at a pinned digest, through a three-line shape
that also lets CI substitute a locally built base:

```dockerfile
ARG BASE_IMAGE=pinned-base
FROM ghcr.io/syndic/unnatural_designs-devcontainer-base:latest@sha256:<digest> AS pinned-base
FROM ${BASE_IMAGE}
```

with the selector in `devcontainer.json`:

```jsonc
"build": { "args": { "BASE_IMAGE": "${localEnv:BASE_IMAGE:pinned-base}" } }
```

Every line of that is load-bearing, and the shapes it rules out fail quietly:

- **The override has to travel through `build.args`.** `devcontainers/ci` has no build-arg input,
  so an env var is the only channel, and `${localEnv:}` is the only thing `devcontainer.json` can
  interpolate.
- **The substitution needs a default.** A bare `"${localEnv:BASE_IMAGE}"` passes
  `--build-arg BASE_IMAGE=` when the variable is unset, and an explicitly empty build arg
  overrides the Dockerfile's `ARG` default — breaking `FROM` for every local `devcontainer up`.
- **The default cannot be a reference.** The CLI splits the substitution on `:` and takes field 2,
  so `"${localEnv:BASE_IMAGE:ghcr.io/…@sha256:…}"` silently truncates. A digest can never be a
  substitution default; a colon-free stage alias can.
- **`AS pinned-base` must precede the consuming `FROM`.** That is what makes Renovate read
  `FROM ${BASE_IMAGE}` as an internal stage reference and skip it. Drop the alias and Renovate
  emits a bogus `pinned-base` dependency instead.
- **The reference carries a tag *and* a digest**, for the same reason `oci.pull` does below: the
  digest pins the build, the tag is what Renovate compares against.

This repo is the first consumer, so its `.devcontainer/` is the worked example, and
`.devcontainer/test_devcontainer_config.py` asserts the couplings above from the files themselves.
CI sets `BASE_IMAGE=devcontainer-base:ci` — the tag `bazel run :load` produces — whenever a PR
touches the base, so a base change is smoke-tested against a real consumer *before* it publishes,
rather than a Renovate bump later.

Note the freshness trade-off the image channel buys: `devcontainer up` reuses an existing
container, so a base bump lands on the next rebuild, not the next `up`.

### Signed commits under the devcontainer CLI

Not in the image yet — these pieces live in each consumer's `devcontainer.json` and
`post-start.sh`. The rationale is here because both repos need it and both would otherwise carry a
drifting copy; the code moves into the library when the second consumer needs it.

The host signs with **SSH** (`gpg.format = ssh`, `commit.gpgsign = true`, no explicit
`user.signingkey` — `gpg.ssh.defaultKeyCommand` shells out to `ssh-add -L`). That makes two things
load-bearing inside the container: a usable ssh-agent socket the in-container `git` can reach, and
the host's `~/.gitconfig`. Pushing the resulting signed commit then needs a third — the host's
`~/.ssh/known_hosts`, or SSH refuses the unknown github.com fingerprint. *Verifying* it locally
needs a fourth — the allowed-signers file named by `gpg.ssh.allowedSignersFile`, or
`git verify-commit` reports a good-but-untrusted signature (`%G?` → `U`). VS Code's Dev Containers
extension supplies the first three automatically — it forwards the host ssh-agent through the VS
Code Server's own SSH tunnel (a per-user socket published by the server process, *not* Docker
Desktop's magic socket — that mount isn't even present in extension-launched containers), and
bridges the host gitconfig + known_hosts between `postCreate` and `postStart`. It does not bridge
the allowed-signers file. The `devcontainer` CLI does none of it. The fix is five additive pieces —
two bind mounts and three scripted steps:

- **SSH agent** — `devcontainer.json` binds Docker Desktop's magic socket
  `/run/host-services/ssh-auth.sock` (Desktop's documented mechanism for exposing the host's
  `ssh-agent` to any container on macOS) and sets `SSH_AUTH_SOCK` via `containerEnv`. The macOS
  launchd path is *not* used: it's unreachable inside the container and rotates across reboots.
  Trade-off: the mount is Docker-Desktop-specific and would dangle under colima / OrbStack /
  podman. A socat-based engine-agnostic relay is the known alternative; not worth the setup until
  a non-DD engine is on the table. Docker Desktop intercepts that path even though it isn't
  physically on the host; on other engines it isn't intercepted *and* doesn't exist, so the bind
  would fail at container start. `initialize.sh` checks `docker info` for "Docker Desktop"; if
  absent it `sudo touch`es a placeholder at the magic path so the bind succeeds (agent forwarding
  won't be functional there, which is fine — CI's smoke job just needs the container to start).
- **SSH agent socket ownership** — Docker Desktop bind-mounts the magic socket root-owned mode
  660. The remoteUser is `vscode` (uid 1000), so it can't connect to a root-owned socket.
  `post-start.sh` `chown`s it to the current user (vscode has passwordless sudo in the
  `devcontainers/base:debian` image). Has to happen on every container start, because the
  bind-mounted socket is re-created root-owned each time. Harmless under VS Code, which uses its
  own tunneled socket and ignores the magic one entirely.
- **`~/.gitconfig`** — `initialize.sh` writes a snapshot to `.git-plumbing/host-gitconfig`.
  `post-start.sh` copies it to `$HOME/.gitconfig` *only if that file is missing or empty*.
  `postStartCommand` runs after the Dev Containers extension's own gitconfig copy, so the
  empty-check naturally lets VS Code win when it's involved. It stays a snapshot rather than
  becoming a bind mount like the two below, because the container *rewrites* it (see the
  allowed-signers repoint): a read-only mount would break the repoint and a read-write one would
  leak container edits back to the host.
- **`~/.ssh/known_hosts`** — bound from `.host-known-hosts`, a symlink `initialize.sh` points at
  the file ssh itself would write. Without it, `git push` from inside the CLI-launched container
  fails with "Host key verification failed" on first contact with github.com — the base image's
  `~/.ssh` is empty and SSH refuses unknown fingerprints by default. Deliberately **writable**:
  Docker resolves the symlink host-side, so accepting a new host inside any container writes
  through to the host's real file and every later container starts with it. Without that, an
  unknown-but-legitimate host would have to be re-accepted in every container forever. The target
  is discovered from `ssh -G` (`UserKnownHostsFile`, first entry — the only one ssh appends to),
  falling back to `~/.ssh/known_hosts`. An empty target is created if absent, which ssh would do
  itself.
- **`~/.ssh/allowed_signers`** — the trust set `git verify-commit` checks a signature against,
  bound the same way from `.host-allowed-signers` but **read-only**: nothing should ever write the
  trust set. Git reads it only for verification, never for signing, which is why commits signed
  fine before this piece existed while `git log --show-signature` printed "Unable to open allowed
  keys file…" / "No principal matched". The symlink follows `gpg.ssh.allowedSignersFile` wherever
  it points, so that setting stays authoritative; when it's unset or unreadable the link falls back
  to an empty file under `.git-plumbing/`, which keeps the bind from dangling without creating
  anything in the user's `~/.ssh`. Contents are public keys and principal emails — no secret
  material.

**Why symlinks rather than `${localEnv:HOME}` paths.** Same reason as `.host-git-common`: `mounts`
are resolved at config-parse time and can only interpolate `${localEnv:}` /
`${localWorkspaceFolder}`, so naming the host's real path would hardcode a layout no shared
artifact has any business dictating. Presenting a fixed *shape* — a symlink at a known
workspace-relative name, whose target the host stub chooses — keeps the host free to store these
files anywhere. Known limitation: a single-file bind means whole-file *replacement* on the host
(e.g. `ssh-keygen -R`) is only picked up because Docker Desktop re-resolves the path; a plain Linux
bind mount pins the inode and would show stale content until restart.

One consequence of these being mounts: Docker creates the mount parent root-owned 0755 before any
hook runs, and SSH ignores a `~/.ssh` it considers unsafe, so `post-start.sh` `install -d`s the
directory back to the user at mode 700 — without touching the binds inside it.

The repoint the mounts don't remove: the copied gitconfig still points
`gpg.ssh.allowedSignersFile` at the *host-absolute* path (`/Users/...`), which doesn't exist in the
container, so `post-start.sh` rewrites it (`git config --global`) to the mounted path whenever that
file is non-empty. Keying the rewrite on the *destination* rather than on "did we just copy" makes
it fire under VS Code too — the extension bridges the gitconfig, stale path and all, but not the
file it names — and honours an allowed_signers a user provisioned some other way.

## Conventions

- **RUN-free, structurally.** With nothing executed at build time, producing both
  `linux/amd64` and `linux/arm64` needs no QEMU — which matters because developer hosts are
  Apple Silicon and CI runners are amd64. This used to be a convention a comment asked people
  to honour; since the image is assembled by `rules_oci`, which cannot execute a build step at
  all, it is now a property of the toolchain. Reaching for a `RUN` means leaving Bazel.
- **No baked features.** The devcontainer CLI applies each consumer's `features` block on top
  of this image and does not dedupe against it, so baking them would either waste build time
  or pull the feature pins and `devcontainer-lock.json` out of the repos that consume them.
- **The plumbing stays shell, and is tested from Python.** It can't be Python: the Debian base
  ships no interpreter, and adding one costs the RUN-free property above. So the decision logic is
  factored into side-effect-free `plumbing_*` / `initialize_*` functions and the tests source the
  scripts to exercise them under `bazel test //...` — `test_plumbing.py` here for the library,
  `.devcontainer/test_initialize.py` for this repo's host stub, each next to the code it covers.
  `shellcheck` covers the rest, wired as both a pre-commit hook and a CI job.
- **`lib.sh` sets no shell options.** It is sourced into callers that own their own; the
  dispatcher sets `-euo pipefail`.
- **Failure policy is per step.** The git-common bridge fails loud — the consumer's postCreate
  runs git immediately after. Timezone and the other conveniences warn and continue, so a
  cosmetic problem never aborts container creation.
- **Decisions are pure functions.** Effects live at the call site. That is what lets
  `test_plumbing.py` source `lib.sh` and exercise the logic without privileged writes. When adding
  logic here, put the decision in a pure function and leave only the effect at the call site.

## Build and publish

The image is assembled by Bazel, not a Dockerfile, so it rides `bazel build //...` like
everything else — one command, exercised locally and in CI. That also means no Docker daemon is
needed to build it, which is what makes it work inside this repo's own devcontainer (which has
neither a docker client nor a mounted socket).

| command | needs |
| --- | --- |
| `bazel build //meta/devcontainer-base:image` | nothing — runs in the devcontainer |
| `bazel test //meta/devcontainer-base/...` | nothing — layer layout and modes are asserted from the tars |
| `bazel run //meta/devcontainer-base:load` | a Docker daemon; loads `devcontainer-base:ci` |
| `bazel run //meta/devcontainer-base:push` | credentials; pushes the index, no daemon |

Because `pkg_tar` pins timestamps, rebuilds are byte-identical and the image digest is stable —
so the same source produces the same digest on any machine. A `docker build` never did: it bakes
fresh timestamps into the image config, which is why an equivalent rebuild still looked different.

CI builds this whenever `meta/devcontainer-base/` **or `MODULE.bazel`** changes — the latter is
where this image's own base is pinned — and runs the dispatcher inside it on **both**
architectures: native `ubuntu-latest` and `ubuntu-24.04-arm` runners, so no QEMU is involved. Both
are smoke-tested because `oci_load` can only materialise one image per Docker tag; on an amd64
runner alone, the arm64 manifest would ship having only ever been assembled, and arm64 is what
developer hosts actually run. The same classification runs this repo's own devcontainer build
against the candidate image, which is the consumer-side half of the gate.

Publishing happens on pushes to `main` only, in a job gated on both smoke jobs, so a base that
failed on either architecture never reaches the registry. `oci_push` publishes the exact index
Bazel built rather than rebuilding it.

Consumers pin a digest and Renovate bumps it: its `bazel-module` manager reads `oci.pull` as a
`docker` dependency, which is why the base is declared with both a tag and a digest — a digest
alone would be pinned forever with nothing to compare against.

Those digest bumps **automerge** — the repo's only automerged dependency, via the
`devcontainer base image` rule in `renovate.json`. Three things make that safe, and it stops
being safe if any of them change:

- The bump is gated by the same checks as any other PR, including `Base image (all platforms)`
  and this repo's own devcontainer build, so an upstream base that breaks either can't land. That
  gating is why `MODULE.bazel` is in the workflow's path classification; without it the bump would
  move the pin and trigger nothing at all.
- The change is a digest and nothing else. A digest bump leaves `MODULE.bazel.lock` valid — the
  `oci` extension records no `moduleExtensions` entry because every pull is digest-pinned and
  therefore reproducible — so no derived-file regeneration has to race the merge.
- It is deliberately *not* folded into the `all non-major dependencies` group. Grouped, a base
  bump could not merge whenever anything else in that group was red, which is exactly when you
  would want the base moving on its own.

The GHCR repository is declared once, on the `oci_push` target in `BUILD.bazel`; it is a
property of the artifact rather than of the runner, so it does not appear in the workflow.

The GHCR package must be **public** for Mend-hosted Renovate to read its digests; a private
package makes Renovate silently stop producing updates. That is safe here because the image
contains no host-specific content — two shell scripts and a Debian base.
