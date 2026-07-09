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
still commits — see "Auto-commit GitHub App" below), and (e) remove the freshness check in
`meta/scripts/check_modules.py`.

---

## Auto-commit GitHub App (`Renovate helper`)

One workflow uses the helper bot on Renovate PRs: `.github/workflows/renovate-derived-files.yml`.
It refreshes both sets of derived lock files, in a fixed order, and commits them together.

- **Python** — **ratifies Renovate's dep proposals via uv** and re-derives both `uv.lock` and
  `requirements_lock.txt`. Renovate's `pep621` cannot run `uv lock` on Mend hosted
  (`allowedUnsafeExecutions` allowlist gate), so it falls back to `pip_requirements` editing
  `requirements_lock.txt` in place. The workflow runs
  [`meta/scripts/ratify_renovate_proposals.py`](../meta/scripts/ratify_renovate_proposals.py),
  which extracts the proposed package names from the diff, runs `uv lock --upgrade-package <each>`
  so uv is the actual resolver, re-exports `requirements_lock.txt`, and reports any packages uv
  refused to advance (workspace constraint conflicts). On conflict the workflow files a
  `REQUEST_CHANGES` review with the script's per-package diagnosis rather than silently reverting
  Renovate's signal. Dismissing the review is the deliberate ack that the bump is impossible
  (close the PR) or that the constraint will be relaxed (push to the branch to re-evaluate).
  Prior bot `REQUEST_CHANGES` reviews are dismissed after the new state lands so the PR's review
  status always reflects the latest evaluation. The data manipulation (diff parsing, pep440
  comparison) is in the Python script under tests
  (`//meta/scripts:test_ratify_renovate_proposals`); the workflow handles only Actions-context
  side effects (token mint, GitHub API calls, composite action invocation).
- **Bazel** — re-updates `MODULE.bazel.lock` via `bazel mod deps --lockfile_mode=update` (same
  allowlist story), skipped if the Python half reported conflicts. Mechanical: there is no
  equivalent of uv's "constraint refuses to advance" failure mode.

The order is load-bearing. `pip.parse` reads `requirements_lock.txt`, and the artifact hashes it
resolves are recorded in `MODULE.bazel.lock`'s `facts` — so uv must settle `requirements_lock.txt`
before `bazel mod deps` regenerates the lock, or the two committed files disagree. That is also why
a Python-only PR still triggers the Bazel refresh. These were two path-triggered workflows until
Renovate's `all non-major dependencies` group made the both-manifests PR the common case: separate
workflows cannot express the ordering (GitHub queues runs by arrival), and their two
`createCommitOnBranch` mutations raced on `expectedHeadOid`. One workflow, one commit, no race.

The workflow delegates the actual commit to a shared composite action
`.github/actions/commit-file-via-app/`, which wraps:

- minting an installation token from the helper app (`actions/create-github-app-token`),
- diff-checking each requested file (no-op if Renovate's update was already complete),
- calling the GraphQL `createCommitOnBranch` mutation (web-flow signing satisfies branch
  protection; the app-token actor identity makes the resulting push event retrigger downstream
  required status checks — `GITHUB_TOKEN` would suppress them).

The action's inputs and no-op behavior are a public contract with consumers outside this repo —
see [its README](../.github/actions/commit-file-via-app/README.md) for the contract, consumer
list, and self-test workflow.

### App permissions

The helper app needs `Contents: read & write` (to commit) **and `Pull requests: read & write`**
(to file and dismiss `REQUEST_CHANGES` reviews on conflict). Both are configured in the app's
settings on GitHub; no code change.

### App identity in `gitIgnoredAuthors`

`renovate.json` lists the helper bot's commit-author email under `gitIgnoredAuthors` so Renovate
does not treat the auto-commit as "user modified this branch" — without it, Renovate suppresses
its own follow-up actions (rebase, additional dep bumps within the same group) on any branch we
have touched.

The match is exact-string only ([renovate/lib/util/git/index.ts:885](https://github.com/renovatebot/renovate/blob/main/lib/util/git/index.ts#L885)
is a `Set.delete(value)` call — no wildcard, no regex). The email shape is
`<numeric-id>+<app-slug>[bot]@users.noreply.github.com`; both halves change if the app is recreated.

**If the app is ever recreated, recovery is:**

1. Wait for the next Renovate PR where our auto-commit fires.
2. Read the new author email off it:
   `gh api repos/Syndic/unnatural_designs/pulls/<n>/commits --jq '.[].commit.author.email'`
3. Update `gitIgnoredAuthors` in `renovate.json` with the new value.

Until that update lands, Renovate will react to the helper's commits as if they were user
edits — the same state we were in before this entry existed. Visible but not load-bearing.

**Trigger to revisit:** if every workflow that uses the helper bot goes away (e.g. `rules_python`
lands uv-native lockfile support per the entry above, AND `MODULE.bazel.lock` is either
auto-managed by Bazel or by a Mend allowlist change that lets Renovate update it directly),
the app can be uninstalled from the repo and its key destroyed (and the `gitIgnoredAuthors`
entry removed in the same change).

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
