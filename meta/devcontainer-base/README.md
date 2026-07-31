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

- **RUN-free, structurally.** With nothing executed at build time, producing both
  `linux/amd64` and `linux/arm64` needs no QEMU — which matters because developer hosts are
  Apple Silicon and CI runners are amd64. This used to be a convention a comment asked people
  to honour; since the image is assembled by `rules_oci`, which cannot execute a build step at
  all, it is now a property of the toolchain. Reaching for a `RUN` means leaving Bazel.
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

CI builds this whenever `meta/devcontainer-base/` changes and runs the dispatcher inside it on
**both** architectures — native `ubuntu-latest` and `ubuntu-24.04-arm` runners, so no QEMU is
involved. Both are smoke-tested because `oci_load` can only materialise one image per Docker
tag; on an amd64 runner alone, the arm64 manifest would ship having only ever been assembled,
and arm64 is what developer hosts actually run.

Publishing happens on pushes to `main` only, in a job gated on both smoke jobs, so a base that
failed on either architecture never reaches the registry. `oci_push` publishes the exact index
Bazel built rather than rebuilding it.

Consumers pin a digest and Renovate bumps it: its `bazel-module` manager reads `oci.pull` as a
`docker` dependency, which is why the base is declared with both a tag and a digest — a digest
alone would be pinned forever with nothing to compare against.

Those digest bumps **automerge** — the repo's only automerged dependency, via the
`devcontainer base image` rule in `renovate.json`. Three things make that safe, and it stops
being safe if any of them change:

- The bump is gated by the same checks as any other PR, including `Base image (all platforms)`,
  so an upstream base that breaks the dispatcher can't land.
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
