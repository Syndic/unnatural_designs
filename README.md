# Unnatural Designs

Joshua Yanchar's personal projects monorepo.

## Repository Organization

| Directory     | Purpose                                                                        |
| ------------- | ------------------------------------------------------------------------------ |
| `//apps/`     | End-user-facing applications with persistent UIs (desktop, mobile)             |
| `//docs/`     | Documentation not tightly coupled to a specific project                        |
| `//infra/`    | Infrastructure-as-code: Terraform, Helm, Pulumi, etc.                          |
| `//libs/`     | Shared libraries consumed by other packages in this repo                       |
| `//meta/`     | Monorepo-level configuration, automation, and tooling (CI/CD, etc.)            |
| `//services/` | Long-running processes: background daemons and server-side applications        |
| `//tools/`    | Developer and operator-facing CLI tools: short-lived, task-focused executables |

## Dev Environment

The repo ships a
[VS Code Dev Container](https://code.visualstudio.com/docs/devcontainers/containers) at
[`.devcontainer/`](.devcontainer/) so every contributor gets the same toolchain without installing
anything on the host.

**Prerequisites**: [Docker Desktop](https://www.docker.com/products/docker-desktop/) or
[OrbStack](https://orbstack.dev) running on the host, and the VS Code
[Dev Containers extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers).

**To open**: clone the repo, open the folder in VS Code, and run _Dev Containers: Reopen in
Container_ from the Command Palette. First build takes a few minutes; subsequent opens are fast.

**What's inside**: [bazelisk](https://github.com/bazelbuild/bazelisk) (driven by `.bazelversion`),
[`buildifier`](https://github.com/bazelbuild/buildtools/tree/main/buildifier), Go, Python,
[`gh`](https://cli.github.com), [`uv`](https://docs.astral.sh/uv/) (Python package manager),
[`ruff`](https://docs.astral.sh/ruff/) (Python format + lint),
[`ty`](https://docs.astral.sh/ty/) (Python type checker, alpha),
[pre-commit](https://pre-commit.com), and [`golangci-lint`](https://golangci-lint.run). All Python
tools (`ruff`, `ty`, `pre-commit`) are installed via `uv tool install` at image build time, so the
devcontainer has a single Python package manager (uv) and no `pip install --user` in post-create.
Named volumes preserve the two cache roots across container rebuilds: `ud-cache` (`~/.cache` —
Bazel, bazelisk, `go build`, uv, pre-commit) and `ud-go-pkg-cache` (`$GOPATH/pkg` — the Go
module and checksum-db caches, i.e. `/go/pkg`).

**Base image**: all of that is layered on top of
[`meta/devcontainer-base/`](meta/devcontainer-base/README.md)'s published image, which this repo
builds and shares with [Syndic/.dotfiles](https://github.com/Syndic/.dotfiles). It carries the
container-side git/host plumbing (worktree resolution, host timezone, shared-index config). The
Dockerfile pins it by digest, and that pin is a *derived file*: the digest is reproducible from
source, so a pre-commit hook writes it with
[`sync_base_image_pin.py`](meta/scripts/sync_base_image_pin.py) and `bazel test //...` fails when
it drifts — see [Automation](#automation).

**Feature pinning**: the `ghcr.io/devcontainers/features/*` references in
[`devcontainer.json`](.devcontainer/devcontainer.json) are pinned to **full semver**
(`features/go:1.3.4`), not the floating major tags (`:1`) the devcontainer templates emit, and carry
no digests. Both are load-bearing and both have non-obvious reasons — see
[`.claude/CLAUDE.md`](.claude/CLAUDE.md), "plumbing and feature pins". The short version: exact
tags are what make the features Renovate-visible, and that bump is what triggers the
[`devcontainer-lock.json`](.devcontainer/devcontainer-lock.json) regeneration under
[Automation](#automation), which CI then verifies.

**Known limitations**: the Docker and Kubernetes VS Code extensions install but aren't wired to a
daemon or `kubectl` inside the container; BuildBuddy credentials still need host-side setup. See
[`docs/future-considerations.md`](docs/future-considerations.md) for the open items.

**Commit signing**: VS Code's Dev Containers extension copies your host `~/.gitconfig` into the
container verbatim and does not rewrite filesystem paths inside it. If your host `user.signingkey`
points at a `.pub` file under `/Users/...` or `/home/...`, that path won't resolve inside the
container and `git commit` will fail to sign. The agent itself is forwarded automatically, and
`allowed_signers` is copied and its path rewritten — only `user.signingkey` is left untouched.
[vscode-remote-release #7796](https://github.com/microsoft/vscode-remote-release/issues/7796) tracks
this gap.

Two host-side configurations work cleanly inside the container, in order of preference:

1. **Dynamic key resolution via `gpg.ssh.defaultKeyCommand` (recommended).** Git asks the forwarded
   ssh-agent for the signing key at sign time, so the same gitconfig works on the host and in any
   devcontainer, and survives key rotation without edits. The one-liner below prefers a key whose
   comment matches your `user.email` and falls back to the first key in the agent. The script must
   be inlined in the gitconfig (rather than referenced as a file) because no single host path is
   guaranteed to exist inside every devcontainer that copies your gitconfig.

   ```bash
   git config --global --unset user.signingkey
   git config --global gpg.ssh.defaultKeyCommand "$(cat <<'CMD'
   sh -c 'KEYS=$(ssh-add -L 2>/dev/null); [ -z "$KEYS" ] && exit 1; EMAIL=$(git config user.email); SEL=$(echo "$KEYS" | awk -v e="$EMAIL" "\$NF==e{print;exit}"); [ -z "$SEL" ] && SEL=$(echo "$KEYS" | head -n1); [ -z "$SEL" ] && exit 1; printf "key:: %s\n" "$SEL"'
   CMD
   )"
   ```

2. **Inline the public key literal in `user.signingkey`.** Replace the `.pub` path with the literal
   key bytes. The copied gitconfig is then valid in the container without any indirection. Simpler
   than option 1, but you must update this value if you rotate your signing key.

   ```bash
   git config --global user.signingkey "$(ssh-add -L | head -n1)"
   ```

## Build System

This repo uses [Bazel](https://bazel.build) with [Bzlmod](https://bazel.build/extern/bzlmod) for
dependency management. The Bazel version is pinned in `.bazelversion` and managed via
[Bazelisk](https://github.com/bazelbuild/bazelisk).

Go build files are generated and maintained by
[Gazelle](https://github.com/bazelbuild/bazel-gazelle). After modifying Go source files, run:

```
bazel run //:gazelle
```

After adding new external Go dependencies, run:

```
go get github.com/example/pkg
bazel mod tidy
bazel run //:gazelle
```

### Remote Cache and Execution

Builds use [Buildbuddy](https://buildbuddy.io) for both remote caching and remote execution (on
Linux x86_64 and arm64). CI is configured automatically.

For local use, add your API key to `.bazelrc.user` (gitignored):

```
common --remote_header=x-buildbuddy-api-key=YOUR_KEY
```

The remote cache is enabled by default on every Bazel invocation. Additional configs:

| Config               | Use when                                                                            |
| -------------------- | ----------------------------------------------------------------------------------- |
| _(default)_          | Normal local/IDE/pre-commit usage - remote cache reads and writes, local execution. |
| `--config=remote_bb` | Offload builds to BuildBuddy's remote executors (linux_x86_64 and linux_arm64).     |
| `--config=ci`        | Used by GitHub Actions: remote executors (via `:remote_bb`) + BES reporting.        |
| `--config=local`     | Disable all remote features (offline, or debugging cache issues).                   |

Remote-executor configs are suffixed with the backend they target (`_bb` = BuildBuddy). Additional
backends in the future would follow the same naming pattern. `darwin_arm64` has no remote executor;
its platform config marks every action `no-remote-exec`, so actions always execute locally while
still reading and writing the remote cache.

Target platform shortcuts are also available: `--config=linux_x86_64`, `--config=linux_arm64`,
`--config=darwin_arm64`. See [`//platforms`](platforms/BUILD.bazel) for the platform definitions.

### Pure-Go policy

The build is **pure-Go by policy**. [`meta/scripts/check_no_cgo.py`](meta/scripts/check_no_cgo.py)
runs as the `no-cgo-check` CI job and rejects both direct `import "C"` and any transitive dependency
that compiles native code. The rationale is hermeticity and build simplicity: no LLVM toolchain, no
sysroots, no Apple SDK handling, and Linux outputs are statically linked (no glibc dependency) so
they drop into `FROM scratch` containers directly.

CI builds and tests every PR against each supported platform (`linux_x86_64`, `linux_arm64`,
`darwin_arm64`) — each row of the matrix runs on a host of the matching arch (`ubuntu-latest`,
`ubuntu-24.04-arm`, `macos-latest`) so the build host always equals the target. Linux rows
register their matching BuildBuddy executor as an exec candidate via `--config=<platform>`;
darwin executes on the runner because BB has no macOS executors. No emulation layer (qemu,
Rosetta) anywhere in the build. A change that breaks any platform fails CI before it can land.

Local builds default to the host platform. To build for a different target, use the
platform-shortcut config:

```
bazel build //tools/... --config=linux_arm64
```

Building locally for a non-host target works today because the toolchain is pure-Go, but this is not
a guaranteed property of the repo - it is a side effect of the current policy and may not survive
future toolchain changes.

## CI

Three GitHub Actions workflows run on every push and pull request to `main`.

**CI** - code-change-driven checks:

| Job                          | Trigger condition                                                                                  |
| ---------------------------- | -------------------------------------------------------------------------------------------------- |
| Gazelle check                | Always - verifies BUILD files match source                                                         |
| MODULE.bazel.lock freshness  | Always - verifies `bazel mod tidy` leaves MODULE.bazel and its lock unchanged                      |
| Module completeness check    | Always - verifies Go module matrix/config and Python workspace/lock invariants                     |
| go.work check                | Always - verifies all Go modules are registered in `go.work`                                       |
| Secrets check                | Always - verifies the `secrets/` directory contains no committed files                             |
| No-cgo policy check          | Always - rejects `import "C"` and transitive deps that compile C/C++/cgo/SWIG                      |
| golangci-lint                | After module check passes - runs per Go module                                                     |
| ruff                         | Always - `ruff format --check` and `ruff check` over all Python                                    |
| shellcheck                   | Always - lints every tracked `*.sh`                                                                |
| ty                           | Always - `uvx ty@<pin> check` (Astral's static type checker, alpha) over all Python                |
| Build and test               | After all checks above pass                                                                        |
| Coverage                     | After build and test - `bazel coverage //...`, uploads merged lcov to Codecov                      |

**Security** - also runs on a weekly schedule (Mondays at 02:00 UTC):

| Job                          | Purpose                                                                         |
| ---------------------------- | ------------------------------------------------------------------------------- |
| Module completeness check    | Gate for the per-module security jobs below                                     |
| Semgrep                      | SAST - scans for injection flaws, insecure API usage, and hardcoded secrets     |
| govulncheck                  | Dependency CVE scanning - checks reachable call paths against the Go vuln DB    |
| govulncheck-all              | A single static target that github can require pass for branch protection rules |
| pip-audit                    | Dependency CVE scanning for Python - manifest-based scan over the uv resolution |
| Trivy                        | Supply chain and filesystem scanning - secrets, CVEs across all ecosystems      |

**Devcontainer** - builds the devcontainer image and smoke-tests the toolchain it ships
(`bazel --version`, `go version`, `python3 --version`). The job is gated on a path diff against
the PR base: it only runs the build when `.devcontainer/` or `.github/workflows/devcontainer.yml`
changed in this PR, and reports success otherwise so the status check always reports.

## Automation

**Pre-commit hooks** (via [pre-commit](https://pre-commit.com)) run a narrow set of checks before
each commit. To install:

```
pre-commit install
```

Only hooks that either fix the problem they detect (`bazel-mod-tidy`, `gazelle`, `uv-lock-fresh`,
`ruff-check`, `ruff-format`) or prevent unsafe content from entering the repo (`check-secrets-dir`)
run here. Verification-only checks live in the editor instead (see **Editor integration** below) so
they can surface findings without blocking a commit when you want to switch contexts.

| Hook                | Triggers on                                  |
| ------------------- | -------------------------------------------- |
| `bazel-mod-tidy`    | `go.mod`, `go.work`, `go.sum`                |
| `uv-lock-fresh`     | `pyproject.toml`, `uv.lock`, `requirements_lock.txt` |
| `ruff-check`        | `*.py` files                                 |
| `ruff-format`       | `*.py` files                                 |
| `gazelle`           | `*.go` files                                 |
| `check-secrets-dir` | files under `secrets/`                       |

**Editor integration** (via `.vscode/`) - runs the non-fixing checks on save. Works in VS Code and
VS Code-derived editors (e.g. Google Antigravity). Recommended extensions
(`.vscode/extensions.json`):

- [`golang.go`](https://marketplace.visualstudio.com/items?itemName=golang.go) - runs
  `golangci-lint` on save at package scope, surfacing inline findings that match what CI enforces.
- [`charliermarsh.ruff`](https://marketplace.visualstudio.com/items?itemName=charliermarsh.ruff) -
  surfaces `ruff check` diagnostics inline and applies `ruff format` on save, matching what the CI
  `ruff` job and the pre-commit hooks enforce.
- [`astral-sh.ty`](https://marketplace.visualstudio.com/items?itemName=astral-sh.ty) - surfaces
  `ty check` diagnostics inline, matching what the CI `ty` job enforces. Config lives in
  `[tool.ty]` in `//:pyproject.toml`.
- [`gruntfuggly.triggertaskonsave`](https://marketplace.visualstudio.com/items?itemName=Gruntfuggly.triggertaskonsave) -
  triggers the repo-health scripts on save (wired via `triggerTaskOnSave.tasks` in
  `.vscode/settings.json`).
- [`ryanluker.vscode-coverage-gutters`](https://marketplace.visualstudio.com/items?itemName=ryanluker.vscode-coverage-gutters) -
  paints gutter marks in Go files from a local `bazel coverage //...` run.

| On-save check        | Triggers on                                |
| -------------------- | ------------------------------------------ |
| `golangci-lint`      | `*.go` files                               |
| `ruff` (diagnostics + format) | `*.py` files                      |
| `ty` (type diagnostics)       | `*.py` files                      |
| `check-modules`      | `go.mod`, `pyproject.toml`, `uv.lock`, `requirements_lock.txt`, workflow `.yml`, `.golangci.yml` |
| `check-go-work`      | `go.mod`, `go.work`                        |

**Viewing coverage locally**: run `bazel coverage //...` from the repo root, then open the Command
Palette and pick _Coverage Gutters: Display Coverage_ (or _Watch_ for live updates). The merged lcov
lives at `bazel-out/_coverage/_coverage_report.dat`; the extension is pre-configured in
[`.vscode/settings.json`](.vscode/settings.json) to find it there. CI uploads the same file to
Codecov, so local gutters and the Codecov dashboard reflect the same data.

**Dependency updates** are managed automatically by [Renovate](https://docs.renovatebot.com).
Minor and patch bumps, across every manager, land in a single recurring `all non-major
dependencies` PR. Every other update type gets its own PR, so it is reviewed on its own terms:
`major` (breaking), `replacement` (a package renamed out from under us — `config:recommended`
pulls in `replacements:all`), `rollback`, and `digest`.

`digest` is the one worth calling out. Renovate raises it when a SHA-pinned reference's version
tag resolves to a *different* commit — same version string, different content, i.e. the author
force-moved the tag. That is the single event this repo's SHA pinning exists to catch, so it is
deliberately kept out of the batch where it would be one line of a fifteen-dependency diff.

Every GitHub Action is SHA-pinned, via the `helpers:pinGitHubActionDigestsToSemver` preset in
`extends`. It pins each `uses:` reference to a commit SHA annotated with the full-semver tag that
SHA resolves to (e.g. `actions/checkout@<sha> # v7.0.1`). The *full* version in the comment is what
makes routine upgrades land as `minor`/`patch` and batch: Renovate advances the SHA and rewrites the
version. A bare-major comment like `# v7` would instead track the floating `v7` tag, so every `v7.x`
release would move the SHA under a fixed version string — a `digest` update on its own PR. The
preset's `extractVersion`/`versioning` rules keep the comment on a full-semver tag *through*
updates, but they cannot expand the comment a pin starts from: `pinDigest` copies whatever version
string the `uses:` referenced, so a source tag written as bare `@v7` pins to `# v7` and stays there
(it will not self-heal — under the preset's versioning `v7` and `v7.0.0` compare equal, so Renovate
sees no update to offer). **Always reference a full three-component tag when adding or bumping a
`uses:` entry** (`actions/checkout@v7.0.0`, not `@v7`), so the resulting pin carries a full-semver
comment. Semgrep is the one action that could not join this scheme: `semgrep/semgrep-action` was deprecated
and frozen at `v0.58.0`, so the SAST job runs the SHA-pinned `semgrep/semgrep` container image
directly instead.

Versions that live in a plain string rather than a manifest Renovate understands are picked up by the
`customManagers` regexes in [`renovate.json`](renovate.json). They come in two flavours, and the
difference matters when adding one:

- **Marker-driven** — a Dockerfile `ARG`, a shell assignment in `post-create.sh`, a workflow
  `with:`/`env:` value. Each expects a `# renovate: datasource=<ds> depName=<name>` comment on the
  line **immediately above** the value, and the value must be quoted in the workflow case
  (`key: "1.2.3"`). The key name is not constrained, so both `version:` and `TY_VERSION:` are
  tracked.
- **Structural** — the two `MODULE.bazel` patterns, which carry `datasourceTemplate`/`depNameTemplate`
  in the config and match the pin site directly (`go_sdk.download(… version = "…")`, and any
  `python_version = "…"`). There is no marker comment to grep for, so these are easy to forget when
  auditing coverage.

Either flavour fails **silently** when the regex stops matching: the pin never moves and Renovate
says nothing. Both have happened here. The workflow clause used to be `[A-Za-z_-]*[Vv]ersion:`, which
no SCREAMING_SNAKE key could satisfy, so the CI `ty` pin sat at an alpha while the devcontainer's
advanced. The `MODULE.bazel` patterns used to require the version to be the *first* argument, which
no real call site satisfied — and the `go_sdk` one matched an explanatory comment instead, yielding a
phantom dependency that looked like coverage. Both are now written to tolerate argument order.

When adding or editing a pattern, confirm it claims the sites you expect **and nothing else** before
relying on it.

The same dependency pinned in several files is one dependency to Renovate: one branch, one PR,
every site edited together — but only while the sites agree. Divergent current values are two
separate updates and drift apart independently, so **keep duplicate pins of a tool byte-identical**
(`ruff` and `ty` are each pinned in both the devcontainer Dockerfile and the CI workflow).

Three grouping exceptions in [`renovate.json`](renovate.json)'s `packageRules` keep *major* bumps
atomic. Each is scoped with `matchUpdateTypes: ["major"]` so it cannot overlap the minor/patch
catch-all — the *grouping* rules match disjoint sets of updates, and the order they appear in does
not matter. (Renovate merges every matching rule in order and the last writer wins, so two rules
setting `groupName` for the same update would be order-dependent. Don't introduce that overlap.)

A fifth rule sets no `groupName` at all: `matchManagers: ["devcontainer"]` with
`pinDigests: false`, which turns off digest pinning for devcontainer features (the reason is in
[`.claude/CLAUDE.md`](.claude/CLAUDE.md), under "plumbing and feature pins"). It overlaps the
grouping rules, which is harmless because it is the only rule that touches `pinDigests`; it sits
last so that stays true if a later rule ever sets the same field.

- **Language toolchain SDKs** — the Go and Python version pins, tracked across `MODULE.bazel`,
  `go.work`, per-module `go.mod`, the workflow `setup-python` steps, the devcontainer Dockerfile's
  `PYTHON_VERSION` arg, and the Go toolchain `version` *option* on the `go` feature in
  `devcontainer.json`. Note the option is a different dependency from the feature reference that
  carries it: `matchDepNames: ["go", "python"]` matches the toolchain option, not
  `ghcr.io/devcontainers/features/go`, so a feature-package major lands ungrouped on its own PR.
- **`ruff`** — pinned in both the devcontainer Dockerfile and the CI workflow. (`pyproject.toml`
  holds ruff's *config*, not its version.)
- **Bazel toolchains and rulesets** — `bazel_dep` majors. Rulesets that must advance in lockstep
  (`rules_go` with `bazel-gazelle`, say) resolve against one another, so splitting their majors
  into separate PRs yields a `MODULE.bazel.lock` that cannot be regenerated until both land.

The catch-all matches the stock `group:allNonMajor` preset, but is written out rather than pulled
in via `extends`, because a preset's `packageRules` merge in *ahead* of the repo's own and the
grouping should not depend on that detail.

One workflow handles the derived lock files Renovate cannot update itself (both refreshes shell
out to a build tool whose execution is blocked by Mend-hosted Renovate's `allowedUnsafeExecutions`
allowlist):

| Workflow | Trigger paths | Re-runs | Commits |
| --- | --- | --- | --- |
| [`renovate-derived-files.yml`](.github/workflows/renovate-derived-files.yml) | `pyproject.toml`, `uv.lock`, `requirements_lock.txt`, `MODULE.bazel`, `.bazelversion`, `**/go.mod`, `go.work`, `.devcontainer/devcontainer.json` | [`meta/scripts/ratify_renovate_proposals.py`](meta/scripts/ratify_renovate_proposals.py) (`uv lock --upgrade-package <each>` + `uv export`), then `bazel mod deps --lockfile_mode=update`, `go mod tidy` + `go work sync`, and `devcontainer upgrade` | `uv.lock`, `requirements_lock.txt`, `MODULE.bazel.lock`, `.devcontainer/Dockerfile` (the base-image pin), the touched `go.mod`/`go.sum` + `go.work.sum`, `.devcontainer/devcontainer-lock.json` |

The uv step runs before the Bazel step, and a Python change triggers the Bazel step when it moves
`requirements_lock.txt`: `pip.parse` reads that file, and the artifact hashes it resolves are
recorded in `MODULE.bazel.lock`'s `facts`, so moving it restales the Bazel lock. A pyproject-only
edit that re-resolves the same versions skips the Bazel step. Both refreshes land in a single commit.

A devcontainer feature bump gets built twice by design: the helper's commit adds
`devcontainer-lock.json`, which matches the `^\.devcontainer/` filter in
[`devcontainer.yml`](.github/workflows/devcontainer.yml), so that workflow runs again on the settled
state rather than only on Renovate's un-regenerated first push. It also re-runs the lock freshness
check, which is what closes the loop: if the refresh step were skipped (a ratify conflict) or failed,
the drifted lock fails the build instead of merging quietly.

The uv step ratifies Renovate's `requirements_lock.txt` edits: it extracts the proposed package
names, asks uv to re-resolve with those packages flagged for upgrade, and either commits the result
or files a `REQUEST_CHANGES` review on the PR (with the script's diagnosis) if uv refuses to
advance a proposed bump. The diff parsing and pep440 comparison live in
[`meta/scripts/ratify_renovate_proposals.py`](meta/scripts/ratify_renovate_proposals.py) under
unit tests (`bazel test //meta/scripts:test_ratify_renovate_proposals`); the workflow handles
only the Actions-context side effects (commit, file/dismiss reviews).

The workflow delegates the actual commit to the shared composite action
[`.github/actions/commit-file-via-app/`](.github/actions/commit-file-via-app/action.yml), which
calls the GitHub GraphQL `createCommitOnBranch` mutation (signed by GitHub's web-flow key) using
an installation token from a dedicated GitHub App rather than the default `GITHUB_TOKEN` — so
required status checks retrigger on the new head. See
[`.claude/CLAUDE.md`](.claude/CLAUDE.md) "Renovate auto-commit helper" for the app mechanism,
its required permissions, and the recovery procedure if the app is recreated.
The action also has consumers outside this repo; its
[README](.github/actions/commit-file-via-app/README.md) documents the compatibility contract,
the consumer list, and the self-test workflow that exercises it on PRs.

A second workflow closes a gap on the other side of the merge. A PR that Renovate automerges
produces no Renovate job, so nothing tells it that `main` moved: every other open Renovate PR then
sits `BEHIND` the new base — unmergeable, since the `main` ruleset requires up-to-date branches —
until the next scheduled run, a window measured at a 6.7 hour median.

| Workflow | Trigger | Does |
| --- | --- | --- |
| [`renovate-run-after-automerge.yml`](.github/workflows/renovate-run-after-automerge.yml) | a PR closed as merged by `renovate[bot]`, or `workflow_dispatch` | ticks the "manual job" checkbox on the Dependency Dashboard, which requests a Renovate run |

No `packageRule` currently sets `automerge`, so the `pull_request` half of that trigger is dormant
and the workflow is reached by `workflow_dispatch` alone. It stays wired because the gap belongs to
automerge itself rather than to any one rule: re-enable automerge anywhere and it starts closing
that gap again with no edit.

That checkbox is Mend's own, not OSS Renovate's, and on the Community tier it is the only way to
request a run — there is no public trigger API. Renovate unticks it during the run it starts, so
the lever re-arms itself. The body edit lives in
[`meta/scripts/renovate_manual_job.py`](meta/scripts/renovate_manual_job.py) under unit tests
(`bazel test //meta/scripts:test_renovate_manual_job`); the workflow handles only the API calls.
See [`.claude/CLAUDE.md`](.claude/CLAUDE.md) "Renovate run after automerge" for the evidence and
for the `platformAutomerge: false` alternative that was considered and rejected.
