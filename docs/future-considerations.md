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

The duplication cost concentrates entirely in the two lifecycle scripts —
`.devcontainer/initialize.sh` and `.devcontainer/post-start.sh` (all three ported fixes landed
there, plus a matching CI smoke assertion). The Dockerfile's `.git-plumbing` COPY/RUN block, the
`devcontainer.json` mount / `workspaceMount` / `SSH_AUTH_SOCK` wiring, and the
`.git-plumbing/README.md` anchor are structurally identical across the repos but stable — not
worth unifying. `post-create.sh` has zero overlap (go/bazel here vs. ansible/uv there) and stays
repo-specific. Textual drift already exists in the shared scripts: comment styles have diverged,
and there are two genuine policy forks — .dotfiles fails loud when the workspace isn't a git
checkout while this repo keeps a graceful else-branch, and the placement of the checkstat config
write differs accordingly.

The plan: extract a canonical `git-plumbing-lib.sh` into this repo holding the shared functions
(host side: resolving the git common dir, the index-portability config, timezone discovery, the
four snapshots, the agent-socket placeholder; container side: `install_ssh_snapshot`, the
gitconfig install, the `allowedSignersFile` repoint, the socket chown). Each repo's
`initialize.sh` / `post-start.sh` becomes a thin driver calling those functions in its preferred
policy — which absorbs the fail-loud-vs-graceful fork instead of forcing one repo to abandon its
documented choice. Consumers vendor copies of the lib — what executes on a developer host is
always a file in the consumer's own tree — but the copies sync automatically rather than by
hand: a workflow in this repo, on any push to main that touches the canonical, commits the new
version to an update PR in each consumer via the `commit-file-via-app` machinery
(web-flow-signed, and the app-attributed push retriggers required checks) with auto-merge
enabled. The consumer's own required checks — its devcontainer smoke job — are the quality gate;
no human action is needed unless those checks go red. A small drift check in each consumer's CI
stays on as the backstop that surfaces a missed or failed propagation as a red check rather than
a silent divergence; it only compares, never executes, what it fetches. Neither path runs remote
code on the host at `up` time, which matters because `initialize.sh` runs on the developer host
and has a sudo branch. This extends the `commit-file-via-app` precedent already established in
this repo: a public contract, a README documenting consumers, and a failure mode of a red check
rather than a silent break. The architectural rationale prose — currently
duplicated and drifting across both repos' CLAUDE.md files — moves next to the lib as its
canonical home, and both CLAUDE.mds shrink to pointers.

Rejected alternatives:

- **A published devcontainer feature.** The feature spec's lifecycle hooks exclude
  `initializeCommand`, and features can't read the workspace build context — the host-side half
  is the load-bearing half of this design, so a feature can't carry it.
- **A git submodule.** An `initializeCommand` referencing a possibly-uninitialized submodule,
  plus submodule-inside-worktree interactions, add fragility exactly where both repos are most
  careful.
- **Fetch-at-runtime** (curl the canonical during `up`): even trusting the canonical's review
  process, this executes whatever main holds at that instant — unpinned (behaviour can change
  mid-branch), broken offline, and with no consumer check between a canonical regression and
  every developer host's sudo branch. The propagation PRs deliver the same hands-off freshness
  through a check-gated commit instead.

**Trigger to revisit:** deliberately deferred — the repos converged in July 2026 (both now carry
all the pieces), so the churn that motivated this may be over. Do the extraction when the *next*
shared plumbing change appears: a third hand-port is the signal that the churn hasn't stopped and
that the fixed cost (lib + two consumer PRs + the propagation workflow + drift checks + doc
moves — on the order of a focused day) is clearly repaid. Until then, hand-porting with a session-level cross-repo check is the accepted
cost.

---
