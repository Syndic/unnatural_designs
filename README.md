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
Named volumes (`ud-bazel-cache`, `ud-go-cache`) preserve the Bazel and Go caches across container
rebuilds.

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
Linux x86_64). CI is configured automatically.

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
backends in the future would follow the same naming pattern. `darwin_arm64` has no remote executor
and always falls back to local execution.

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
| Module completeness check    | Always - verifies Go module matrix/config and Python workspace/lock invariants                     |
| go.work check                | Always - verifies all Go modules are registered in `go.work`                                       |
| Secrets check                | Always - verifies the `secrets/` directory contains no committed files                             |
| No-cgo policy check          | Always - rejects `import "C"` and transitive deps that compile C/C++/cgo/SWIG                      |
| golangci-lint                | After module check passes - runs per Go module                                                     |
| ruff                         | Always - `ruff format --check` and `ruff check` over all Python                                    |
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

**Dependency updates** are managed automatically by [Renovate](https://docs.renovatebot.com). PRs
are grouped where it's useful — Bazel toolchains and rulesets, Go modules, GitHub Actions, language
toolchain SDKs (Go and Python version pins, tracked across `MODULE.bazel` / `go.work` / per-module
`go.mod` / workflow `setup-python` / Dockerfile), Python workspace dependencies (`pyproject.toml`
plus the `uv.lock` it derives), and a dedicated group for `ruff`. Other tracked dependencies
(`ty`, `pip-audit`, `pre-commit`, `buildifier`, `bazelisk`, the `uv` container-image tag) land as
their own PRs. The full set of managers and groups lives in [`renovate.json`](renovate.json).

Two workflows handle derived lock files Renovate cannot update itself (each shells out to a
build tool whose execution is blocked by Mend-hosted Renovate's `allowedUnsafeExecutions`
allowlist):

| Workflow | Trigger paths | Re-runs | Commits |
| --- | --- | --- | --- |
| [`renovate-requirements-lock.yml`](.github/workflows/renovate-requirements-lock.yml) | `pyproject.toml`, `uv.lock`, `requirements_lock.txt` | `uv lock --upgrade-package <each>` + `uv export` | `uv.lock`, `requirements_lock.txt` |
| [`renovate-module-bazel-lock.yml`](.github/workflows/renovate-module-bazel-lock.yml) | `MODULE.bazel` | `bazel mod deps --lockfile_mode=update` | `MODULE.bazel.lock` |

The Python workflow treats Renovate's `requirements_lock.txt` edits as upgrade signals rather
than authoritative state — it asks uv to re-resolve with those packages flagged for upgrade, then
commits the result. If uv refuses to advance a proposed bump (a workspace constraint conflict),
the workflow files a `REQUEST_CHANGES` review on the PR with the diagnosis instead of silently
reverting Renovate's edit. Prior bot reviews are dismissed once the new state lands.

Both workflows delegate the actual commit to the shared composite action
[`.github/actions/commit-file-via-app/`](.github/actions/commit-file-via-app/action.yml), which
calls the GitHub GraphQL `createCommitOnBranch` mutation (signed by GitHub's web-flow key) using
an installation token from a dedicated GitHub App rather than the default `GITHUB_TOKEN` — so
required status checks retrigger on the new head. See
[`docs/future-considerations.md`](docs/future-considerations.md) "Auto-commit GitHub App" for
the rationale, the app's required permissions, and the triggers that would retire each workflow.
