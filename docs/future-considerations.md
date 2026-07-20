# Future Considerations

Items flagged during development as worth revisiting when the time is right.

---

## Local Secret Management

Currently, local Bazel remote cache credentials are stored in `.bazelrc.user` (gitignored). This is
acceptable for a single-user machine but not rigorous. Other secrets are stored in //secrets, which
is gitignored.

A proper solution would retrieve secrets from a secrets manager (e.g. macOS Keychain, 1Password CLI)
rather than storing them in a file on disk.

---

## Renovate Trigger Frequency

Renovate appears to trigger on every branch push rather than only on changes to main. This is more
frequent than expected and worth investigating — it may be a configuration issue or a default
behaviour that can be tightened.

---

## Introducing cgo

The build infrastructure assumes **pure Go** (no cgo). The rationale is build simplicity and
hermeticity: pure-Go means no LLVM toolchain, no sysroots, no Apple SDK handling, and Linux outputs
are statically linked. `rules_go` runs in pure mode via `--@rules_go//go/config:pure` in `.bazelrc`.

The assumption is enforced by [`meta/scripts/check_no_cgo.py`](../meta/scripts/check_no_cgo.py),
which runs as the `no-cgo-check` CI job. It rejects:
- Any `.go` file in this repo containing `import "C"`.
- Any transitive Go dependency that compiles C, C++, cgo, SWIG, or ships pre-built
  `.syso` objects (detected via `go list -deps` with `CGO_ENABLED=1`).

Python is **not** held to an analogous purity policy — see [Python Purity Is Not
Enforced](#python-purity-is-not-enforced) below for that side of the story.

### Why The Current Choice Was Made

We audited cgo usage at the time of this decision and found **zero** uses across the
repo's source and full transitive dep graph (1 module, 198 transitive packages). Every
direct Go dep (vbauerster/mpb, VividCortex/ewma, acarl005/stripansi, clipperhouse/uax29,
mattn/go-runewidth, golang.org/x/sys) is pure-Go. The expected near-term workload
(network-management tooling) does not have an obvious cgo trigger.

Setting up the LLVM + sysroot infrastructure for a hypothetical future need was rejected
as premature complexity. The policy + enforcement check make the assumption explicit
rather than implicit, so a future change is a deliberate decision rather than a quiet
drift.

---

## Python Purity Is Not Enforced

Unlike Go, Python is **not** held to a purity policy. Impure Python deps (wheels with
C extensions — `numpy`, `cryptography`, `pydantic`'s rust core, etc.) are allowed. The
trade-off is that the "any host → any target" property the Go side enjoys does **not**
extend to Python: Python targets are built and tested on a host that matches the target
platform, and CI's per-platform runners cover the supported set.

### Why The Policies Diverge

For Go, purity is cheap to maintain — `--@rules_go//go/config:pure` plus the cgo check is
a few lines of config and a small script, and the Go toolchain already ships every
`GOOS`/`GOARCH` pair. We lose nothing by enforcing it.

For Python the same property is much more expensive to maintain, and the benefit is
smaller:

- **Auto-detection is weak.** Python has no `import "C"` marker. Source-level scans
  (`import ctypes`, `.pyx`, `Extension(...)`) catch the blatant cases but miss the
  dominant failure mode: an innocent-looking `import numpy` whose transitive deps ship
  C. The clean dependency-level check — "every resolved wheel ends in
  `-py3-none-any.whl`" — needs a `rules_python` pip lock file we don't have, and only
  catches issues *after* the lock is generated.
- **The Python ecosystem expects native wheels.** Refusing them closes the door on
  most numerics, crypto, and serialization libraries. The cost of staying pure
  compounds with every dep added.
- **CI matrix coverage is a workable substitute.** If a Python target works on a Linux
  runner and a Mac runner, it works on the platforms we ship to. We don't need
  "build darwin from a Linux host" to work in the abstract; we need "darwin works on
  Macs" to work in practice.

---

## Python BUILD Generation (gazelle_python)

The Go side enjoys gazelle-generated BUILD files. The Python equivalent — the
`rules_python_gazelle_plugin` extension — is **not** wired today: its gazelle
binary depends on `smacker/go-tree-sitter` via CGO, which does not compile under
the repo's pinned Go SDK. The upstream replacement is tracked in
[bazel-contrib/rules_python#3416](https://github.com/bazel-contrib/rules_python/issues/3416);
the pure-Go binding fix is the unmerged
[#3786](https://github.com/bazel-contrib/rules_python/pull/3786).

Until #3786 lands in a `rules_python_gazelle_plugin` release, Python `BUILD.bazel`
files are hand-authored. The cost is low while the Python footprint is small.

**Trigger to revisit:** Renovate will surface the `rules_python_gazelle_plugin`
release containing #3786. That release is the prompt to add the `bazel_dep`,
the polyglot `gazelle_binary` rule, the `modules_mapping`, the
`gazelle_python_manifest`, and widen the gazelle pre-commit hook's `files`
pattern to include `\.py$`.

---

## Drop `requirements_lock.txt` once `rules_python` reads `uv.lock` natively

`rules_python`'s `pip.parse` extension only accepts requirements.txt-format inputs at the pinned
commit (and on `main`). uv.lock is uv's native TOML format, so the chain
`pyproject.toml → uv.lock → requirements_lock.txt → pip.parse` carries a derived file
(requirements_lock.txt) that must be kept in sync with uv.lock by the `uv-lock-fresh` pre-commit
hook and the `Renovate — re-export requirements_lock.txt` workflow.

The drift surface goes away once `rules_python` can point `pip.parse` at `uv.lock` directly.
Upstream tracking: [bazel-contrib/rules_python#3557](https://github.com/bazel-contrib/rules_python/issues/3557)
and the in-flight PR [bazel-contrib/rules_python#3785](https://github.com/bazel-contrib/rules_python/pull/3785).
PEP 751 (`pylock.toml`) is the longer-term ecosystem-wide alternative — parent issue
[bazel-contrib/rules_python#2787](https://github.com/bazel-contrib/rules_python/issues/2787), which
blocks on marker-evaluation work in #2786.

**Trigger to revisit:** the `rules_python` release containing #3785 (or, separately, the PEP 751
work landing). That release is the prompt to (a) switch `pip.parse(..., requirements_lock = ...)`
to whatever the new uv-native attribute is, (b) delete `requirements_lock.txt`, (c) drop the
`uv-lock-fresh` hook's `requirements_lock.txt` re-export, (d) strip the Python half of the
`renovate-derived-files.yml` workflow (the helper app stays installed as long as the Bazel half
still commits — see "Retire the Renovate auto-commit helper" below), and (e) remove the freshness
check in
`meta/scripts/check_modules.py`.

---

## Retire the Renovate auto-commit helper

`.github/workflows/renovate-derived-files.yml` commits regenerated lock files back to Renovate PRs
via the `Renovate helper` GitHub App — it exists only because Mend-hosted Renovate can't run
`uv lock` or `bazel mod deps` itself. The app mechanism, permissions, `gitIgnoredAuthors` gotcha,
and recovery procedure live in [`.claude/CLAUDE.md`](../.claude/CLAUDE.md) "Renovate auto-commit
helper".

**Trigger to revisit:** if every workflow that uses the helper goes away — `rules_python` lands
uv-native lockfile support (see the entry above) AND `MODULE.bazel.lock` becomes auto-managed by
Bazel or a Mend allowlist change lets Renovate update it directly — uninstall the app, destroy its
key, and remove the `gitIgnoredAuthors` entry from `renovate.json` in the same change.

---

## Devcontainer: Docker / Kubernetes Extensions Not Fully Wired

The devcontainer recommends a set of VS Code extensions that mirrors `.vscode/extensions.json`,
including `ms-azuretools.vscode-containers` and `ms-kubernetes-tools.vscode-kubernetes-tools`. These extensions install cleanly but are **not
functional inside the container**:

- The container extension needs access to a Docker daemon. We have not added Docker-outside-of-Docker
  (host socket mount) or Docker-in-Docker (devcontainer feature) — so `docker ps` etc. will fail
  from inside the container.
- The Kubernetes extension expects `kubectl` (and typically `helm` / a kubeconfig) on PATH. The
  `kubectl-helm-minikube` devcontainer feature is not installed, and no kubeconfig is mounted.

This was deliberate: the extensions are harmless if unused, and we don't yet have a concrete
workflow that needs container or cluster access from inside the dev environment. When that
changes — e.g. someone starts iterating on a container image target or a k8s manifest — wire up
the matching feature (and decide on the socket-mount vs. DinD trade-off for Docker) at that point
rather than carrying the complexity speculatively.

---

## Devcontainer: Extract Git Plumbing Shared with Syndic/.dotfiles

This repo and [Syndic/.dotfiles](https://github.com/Syndic/.dotfiles) carry near-identical
devcontainer git plumbing: worktree common-dir bridging (the `initializeCommand` symlink, the
`.git-plumbing` path file, and the Dockerfile's recreation of the host-absolute path), the host
snapshots (gitconfig, known_hosts, allowed_signers), ssh-agent magic-socket forwarding with its
placeholder and chown, host timezone propagation, and the `core.checkstat = minimal` /
`core.trustctime = false` shared-index portability fix. Three fixes from this repo's
[PR #177](https://github.com/Syndic/unnatural_designs/pull/177) were hand-ported to .dotfiles
[PR #99](https://github.com/Syndic/.dotfiles/pull/99) in July 2026.

The duplication cost concentrates in the two lifecycle scripts — `.devcontainer/initialize.sh`
and `.devcontainer/post-start.sh` (all three ported fixes landed there, plus a matching CI smoke
assertion). The `devcontainer.json` mount / `workspaceMount` / `SSH_AUTH_SOCK` wiring and the
`.git-plumbing/README.md` anchor are structurally identical across the repos but stable — not
worth unifying. `post-create.sh` has zero overlap (go/bazel here vs. ansible/uv there) and stays
repo-specific. Textual drift already exists in the shared scripts: comment styles have diverged,
and there are two genuine policy forks — .dotfiles fails loud when the workspace isn't a git
checkout while this repo keeps a graceful else-branch, and the placement of the checkstat config
write differs accordingly.

The insight that shapes the plan is where the host/container boundary actually falls. Every
host-side line either *reads host state* — `git rev-parse --git-common-dir`, the timezone
discovery, the three `cp`s from `$HOME` — or *applies a workaround*. Only the reads are
irreducibly host-side: the data isn't visible in-container, and each read pairs with a
container-side *apply*. Everything else sits on the host by convenience. In particular the
Dockerfile bakes two per-host facts — the git-common-dir symlink and the timezone — into the
image at *build* time, and that is the sole reason the image can't be shared; nothing forces that
work to build time, since the workspace isn't even mounted then.

The plan therefore inverts the earlier idea of extracting a host-side library. Instead, move the
movable application logic to the container side and let a shared base image carry it, shrinking
the irreducibly-per-host residue to a stub:

- **Unbake the image.** Push the git-path symlink and timezone application out of the Dockerfile
  into container lifecycle hooks that read the same mounted `.git-plumbing/` path files at
  runtime, and move the `checkstat`/`trustctime` config writes there too. The git symlink goes at
  the top of `post-create.sh` — it must exist before that script runs git against the worktree,
  so `post-start.sh` is too late; timezone and the config writes are looser on ordering. The
  image ends up fully host-agnostic.
- **Publish a shared base image from this repo.** It carries the container-side plumbing — the
  snapshot installs, the `allowedSignersFile` repoint, the socket chown, and the now-runtime
  symlink / timezone / config application — as reviewed scripts at a known path. Each repo's
  Dockerfile `FROM`s it and layers its own toolchain on top; each repo's container hooks call the
  shared functions, then do repo-specific work. The image is digest-pinned and Renovate-bumped —
  machinery both repos already run — so a new version arrives as an auto-mergeable bump gated by
  each consumer's own devcontainer smoke check, with no new management surface.
- **What's left on the host is a thin read-and-drop stub:** a handful of reads dropping results
  into `.git-plumbing/`, plus the one sudo branch (the agent-socket placeholder). It rarely
  changes and is too small to be worth a shared artifact, so it stays hand-copied — cheaply.
  Keeping that lone host sudo branch repo-local also means shared image code never runs elevated
  on the developer host.
- **Optionally collapse two snapshots to mounts.** known_hosts and allowed_signers are pure
  read-only trust data; each could become a declarative `${localEnv:HOME}/...` bind-mount in
  `devcontainer.json`, removing both its host read and its container install and shrinking the
  stub further. gitconfig can't follow: the container rewrites its `gpg.ssh.allowedSignersFile`,
  so a read-only mount breaks the repoint and a read-write mount leaks container edits to the host.

The architectural rationale prose — currently duplicated and drifting across both repos' CLAUDE.md
files — moves next to the base image as its canonical home; both CLAUDE.mds shrink to pointers.

Rejected alternatives:

- **A shared host-side library** (the earlier direction — a canonical `git-plumbing-lib.sh`
  fetched or vendored by each repo): it shares the wrong half. The churn is container-side; a host
  library abstracts the trivial, stable read-and-drop residue while leaving the churny application
  logic duplicated. The boundary analysis above is what retired it.
- **A published devcontainer feature.** Features can't run `initializeCommand` or read the build
  context, so they can't carry even the thin host stub — and the container half rides the base
  image more simply than a feature would package it.
- **A git submodule.** An `initializeCommand` referencing a possibly-uninitialized submodule,
  plus submodule-inside-worktree interactions, add fragility exactly where both repos are most
  careful.
- **Vendored copies with automated propagation PRs.** App installs, auto-merge and required-check
  configuration, and PR churn in every consumer cost more than the risk they retire once the base
  image's digest bump already delivers hands-off pickup.

**Trigger to revisit:** deliberately deferred — the repos converged in July 2026 (both now carry
all the pieces), so the churn that motivated this may be over. Do the work when the *next* shared
plumbing change appears: a third hand-port is the signal the churn hasn't stopped and that the
fixed cost (unbake the image, stand up and publish the base image, repoint both Dockerfiles, move
the application logic into container hooks, move the docs — on the order of a focused day) is
repaid. One caveat to weigh then: `devcontainer up` reuses an existing container, so a base-image
bump lands on the next rebuild, not the next `up` — the accepted freshness cost of the image
channel. Until then, hand-porting with a session-level cross-repo check is the accepted cost.

---
