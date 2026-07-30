# Shared devcontainer base image

Builds `ghcr.io/syndic/unnatural_designs-devcontainer-base`, which carries the container-side
devcontainer host-plumbing that Syndic repos would otherwise hand-port between each other
(this repo and [Syndic/.dotfiles](https://github.com/Syndic/.dotfiles) had already done that
round trip once). Consumers `FROM` it, digest-pinned, and layer their own toolchain on top.

The architectural rationale for *what* the plumbing does — worktree git resolution, the host
timezone, the shared git index across stat domains, signed commits under the devcontainer CLI
— currently lives in this repo's `.claude/CLAUDE.md`. It moves here when the consumer actually
`FROM`s the image, so both repos' CLAUDE.md files can shrink to pointers.

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

### What the host side still owns

Each consumer keeps its own `initialize.sh`. Its job is to *present* host state in the shape
this library consumes — reads dropped into `.git-plumbing/`, plus symlinks at fixed
workspace-relative names for the host paths only it can discover. It does not constrain where
the host keeps anything. That stub is small and stable, so it stays hand-copied; keeping it
out of the image also means shared code never runs elevated on a developer's host.

## Conventions

- **RUN-free.** With no `RUN` there is nothing to emulate, so `--platform
  linux/amd64,linux/arm64` costs nothing — which matters because developer hosts are Apple
  Silicon and CI runners are amd64. Adding a `RUN` makes multi-arch builds slow.
- **No baked features.** The devcontainer CLI applies each consumer's `features` block on top
  of this image and does not dedupe against it, so baking them would either waste build time
  or pull the feature pins and `devcontainer-lock.json` out of the repos that consume them.
- **`lib.sh` sets no shell options.** It is sourced into callers that own their own; the
  dispatcher sets `-euo pipefail`.
- **Failure policy is per step.** The git-common bridge fails loud — the consumer's postCreate
  runs git immediately after. Timezone and the other conveniences warn and continue, so a
  cosmetic problem never aborts container creation.
- **Decisions are pure functions.** Effects live at the call site. That is what lets
  `test_plumbing.py` source `lib.sh` and exercise the logic without privileged writes.

## Build and publish

CI builds this image in the `Devcontainer` workflow whenever `meta/devcontainer-base/`
changes, and publishes multi-arch on pushes to `main` only — so an unbuilt or unsmoke-tested
base never reaches the registry. Consumers pin a digest and Renovate bumps it.

The GHCR package must be **public** for Mend-hosted Renovate to read its digests; a private
package makes Renovate silently stop producing updates. That is safe here because the image
contains no host-specific content — two shell scripts and a Debian base.
