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

`rules_python`'s `pip.parse` requires a requirements.txt-format input, so the chain
`pyproject.toml → uv.lock → requirements_lock.txt → pip.parse` carries a derived file
(requirements_lock.txt) that must be kept in sync with uv.lock by the `uv-lock-fresh` pre-commit
hook and the `renovate-derived-files.yml` workflow.

`rules_python` 2.2.0 added a `pip.parse(uv_lock = ...)` attribute
([bazel-contrib/rules_python#3785](https://github.com/bazel-contrib/rules_python/pull/3785),
closing [#3557](https://github.com/bazel-contrib/rules_python/issues/3557)), but it does **not**
retire the derived file. Trialled against 2.2.0 on 2026-07-26:

- **A requirements input is still mandatory.** `hub_builder.bzl`'s `_create_whl_repos` calls
  `requirements_files_by_platform()` unconditionally, so `uv_lock` on its own fails analysis with
  "A 'requirements_lock' attribute must be specified". With both attributes set the build is green
  (14/14 targets) and resolution genuinely comes from uv.lock — the materialised wheel repos are
  named with uv.lock's own sha256 prefixes, and the root virtual package (no wheels/sdist)
  correctly produces no hub entry.
- **The advertised consistency check is not implemented.** The upstream docstring claims uv.lock is
  cross-checked against the requirements files when both are given; in practice the requirements
  file is used only to enumerate platform names and its package contents are never compared. Drift
  between the two would be invisible to Bazel.
- Adopting the both-attributes form today would therefore be a net regression: the derived file
  stays on disk but Bazel stops depending on its contents, leaving the pre-commit hook and
  `meta/scripts/check_modules.py` as the only drift detection.
- Worth carrying forward: under `uv_lock` the pip `facts` entries in `MODULE.bazel.lock` disappear
  (~106 lines) because URLs and hashes come straight from uv.lock with no PyPI Simple API
  round-trip. That removes the stated cause of the uv-before-Bazel ordering constraint in
  [`.claude/CLAUDE.md`](../.claude/CLAUDE.md) "Renovate auto-commit helper" — re-check that
  constraint when the drop becomes possible.

PEP 751 (`pylock.toml`) is the longer-term ecosystem-wide alternative — parent issue
[bazel-contrib/rules_python#2787](https://github.com/bazel-contrib/rules_python/issues/2787), which
blocks on marker-evaluation work in #2786.

**Trigger to revisit:** `requirements_lock` becoming *optional* when `uv_lock` is set — not merely
a release carrying uv.lock support, which 2.2.0 already is. The observable signal is
`_create_whl_repos` skipping `requirements_files_by_platform()` when `uv_lock` is present; watch
that call site on a `rules_python` bump. (Or, separately, the PEP 751 work landing.) That is the
prompt to (a) switch `pip.parse(..., requirements_lock = ...)` to the uv-native attribute, (b)
delete `requirements_lock.txt`, (c) drop the `uv-lock-fresh` hook's `requirements_lock.txt`
re-export, (d) strip the Python half of the `renovate-derived-files.yml` workflow (the helper app
stays installed for the Bazel and Go halves), and (e) remove the freshness check in
`meta/scripts/check_modules.py`.

---

## Renovate Custom-Manager Coverage Is Not Enforced

A `customManagers` regex in `renovate.json` that matches nothing — or matches the wrong text — fails
silently: the pin stops moving and nothing reports it. Every instance found so far shares one cause,
a pattern over-fitted to incidental syntax rather than to the thing being pinned:

- **Workflow marker comments.** The clause `[A-Za-z_-]*[Vv]ersion:` could not match a
  SCREAMING_SNAKE key, so `TY_VERSION` (`ci.yml`) and `PIP_AUDIT_VERSION` (`security.yml`) were
  invisible. `ty` sat on an alpha (`0.0.1a25`) while the devcontainer's advanced to `0.0.64`. Clause
  is now `[A-Za-z0-9_-]+:`, which claims any key.
- **`go_sdk.download` in `MODULE.bazel`.** The pattern required `version` to be the *first*
  argument; the real tags list `goarch`/`goos` first. The only text of that shape in the file was an
  explanatory comment containing `go_sdk.download(version="X")`, so Renovate extracted a phantom dep
  with `currentValue = "X"` and silently failed to resolve it, while the three real pins sat at
  `1.26.4` against a `go.work` already on `1.26.5`. **Worse than no match** — a bogus match looks
  like coverage.
- **`python.toolchain` in `MODULE.bazel`.** Same first-argument assumption; a comment and
  `configure_coverage_tool` sit between the paren and `python_version`, so it matched nothing. The
  sibling `pip.parse(python_version = …)` was never targeted by any pattern at all. Both were at
  `3.14` — current, so nothing looked wrong.

That last one is the reason this keeps going unnoticed: a pin that is untracked *and* happens to be
current is indistinguishable from a working one. It only surfaces once upstream moves.

The remaining hole is that coverage is still verified by hand. A repo-health check in the
`meta/scripts/check_*.py` family — read `renovate.json`, walk the files each `managerFilePatterns`
selects, and report both marker comments no `matchStrings` claims *and* patterns that match zero
sites — would make the class of bug impossible. Deferred because it wants the full pattern to be
worth its keep (a `check_renovate_markers.py` plus its unit test, a CI job, a `.vscode/tasks.json`
entry, and README table rows). Two caveats for whoever builds it: Renovate evaluates these patterns
with RE2/JS named-group syntax (`(?<name>`), so a Python implementation has to translate to
`(?P<name>` and cannot assume the two dialects agree on everything; and a zero-match check alone
would not have caught the `go_sdk` case, which matched — just the wrong line.

**Trigger to revisit:** the next time a pin is found not to be tracked, or when a third manager file
pattern is added.

---

## Devcontainer Feature Lock Has No Refresh Mechanism

`.devcontainer/devcontainer-lock.json` pins patch-level digests for the four
`ghcr.io/devcontainers/features/*` features, and nothing updates it automatically. Renovate's
`devcontainer` manager reads only the feature references in `devcontainer.json`, which are floating
major tags (`:2`, `:1`) — so it has something to propose only on a major release. The lock itself is
invisible to every manager, and there is no `devcontainer features upgrade` subcommand to lean on
(checked against CLI 0.88.0). It had drifted two features behind before being refreshed by hand here.

The natural home for a fix is `renovate-derived-files.yml`, which already exists to regenerate
derived files Renovate cannot. The refresh is `rm .devcontainer/devcontainer-lock.json` followed by
`devcontainer build`, then committing the rewritten file through the same `commit-file-via-app`
path. What makes it awkward, and why it is deferred: that workflow is *triggered by* Renovate PRs
touching specific manifests, and a feature-digest refresh has no such trigger — nothing in the repo
changes when upstream publishes `common-utils` 2.5.10. It would need a schedule instead, which is a
different shape from everything else in that workflow.

**Trigger to revisit:** when a stale feature digest actually costs something (a devcontainer bug
already fixed upstream), or when the workflow grows a scheduled trigger for another reason.

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
- **Build and publish the base image from this monorepo.** This fits without new build
  infrastructure: the `Devcontainer` CI job already builds the devcontainer and pushes a cache
  image to GHCR (`ghcr.io/<repo>-devcontainer`, `packages: write`), so the base image is a second
  published target beside it — and this repo's own devcontainer becomes its first consumer,
  `FROM`-ing the base and layering go/bazel on top, so the shared half is dogfooded here on every
  CI run. The base carries the container-side plumbing — the snapshot installs, the
  `allowedSignersFile` repoint, the socket chown, and the now-runtime symlink / timezone / config
  application — as reviewed scripts at a known path; .dotfiles `FROM`s the same image (its GHCR
  package readable org-wide) and layers ansible/uv. Each repo's container hooks call the shared
  functions, then do repo-specific work. The image is digest-pinned and Renovate-bumped —
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
fixed cost (unbake the image, add the base-image target to this repo's `Devcontainer` job,
repoint both Dockerfiles, move the application logic into container hooks, move the docs — on the
order of a focused day) is repaid. One caveat to weigh then: `devcontainer up` reuses an existing
container, so a base-image bump lands on the next rebuild, not the next `up` — the accepted
freshness cost of the image channel. Until then, hand-porting with a session-level cross-repo
check is the accepted cost.

---
