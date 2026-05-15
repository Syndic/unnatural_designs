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

| Config               | Use when                                                                                                 |
| -------------------- | -------------------------------------------------------------------------------------------------------- |
| _(default)_          | Normal local/IDE/pre-commit usage - remote cache reads and writes, local execution.                      |
| `--config=remote_bb` | Offload builds to BuildBuddy's remote executors (linux_x86_64 and linux_arm64).                          |
| `--config=ci`        | Used by GitHub Actions: remote executors (via `:remote_bb`) + BES reporting.                             |
| `--config=local`     | Disable all remote features (offline, or debugging cache issues).                                        |

Remote-executor configs are suffixed with the backend they target (`_bb` = BuildBuddy). Additional
backends in the future would follow the same naming pattern. `darwin_arm64` has no remote
executor and always falls back to local execution.

Target platform shortcuts are also available: `--config=linux_x86_64`, `--config=linux_arm64`,
`--config=darwin_arm64`. See [`//platforms`](platforms/BUILD.bazel) for the platform definitions.

### Pure-Go policy

The build is **pure-Go by policy**. [`meta/scripts/check_no_cgo.py`](meta/scripts/check_no_cgo.py)
runs as the `no-cgo-check` CI job and rejects both direct `import "C"` and any transitive
dependency that compiles native code. The rationale is hermeticity and build simplicity:
no LLVM toolchain, no sysroots, no Apple SDK handling, and Linux outputs are statically
linked (no glibc dependency) so they drop into `FROM scratch` containers directly. See
[`docs/future-considerations.md`](docs/future-considerations.md#introducing-cgo-or-python-c-extensions)
for what would change if cgo were ever introduced.

CI builds and tests every PR against each supported platform (`linux_x86_64`,
`linux_arm64`, `darwin_arm64`) — Linux on `ubuntu-latest` runners dispatching to
matching-arch BuildBuddy executors, darwin on `macos-latest` locally. Tests run
natively on their target arch; there is no emulation layer (qemu, Rosetta) in the
build. A change that breaks any platform fails CI before it can land.

Local builds default to the host platform. To build for a different target, use the
platform-shortcut config:

```
bazel build //tools/... --config=linux_arm64
```

Building for a non-host target works today because the toolchain is pure-Go, but this
is not a guaranteed property of the repo — it is a side effect of the current policy
and may not survive future toolchain changes.

## CI

Two GitHub Actions workflows run on every push and pull request to `main`.

**CI** - code-change-driven checks:

| Job                          | Trigger condition                                                                                  |
| ---------------------------- | -------------------------------------------------------------------------------------------------- |
| Gazelle check                | Always - verifies BUILD files match source                                                         |
| Go module completeness check | Always - verifies every Go module is in the workflow matrices and has linter config                |
| go.work check                | Always - verifies all Go modules are registered in `go.work`                                       |
| Python scale check           | Always - fails if `py_*` target count exceeds threshold (see `meta/scripts/check_python_scale.py`) |
| Secrets check                | Always - verifies the `secrets/` directory contains no committed files                             |
| No-cgo policy check          | Always - rejects `import "C"` and transitive deps that compile C/C++/cgo/SWIG                      |
| golangci-lint                | After module check passes - runs per Go module                                                     |
| Build and test               | After all checks above pass                                                                        |
| Coverage                     | After build and test - `bazel coverage //...`, uploads merged lcov to Codecov                      |

**Security** - also runs on a weekly schedule (Mondays at 02:00 UTC):

| Job                          | Purpose                                                                         |
| ---------------------------- | ------------------------------------------------------------------------------- |
| Go module completeness check | Gate for the per-module security jobs below                                     |
| Semgrep                      | SAST - scans for injection flaws, insecure API usage, and hardcoded secrets     |
| govulncheck                  | Dependency CVE scanning - checks reachable call paths against the Go vuln DB    |
| govulncheck-all              | A single static target that github can require pass for branch protection rules |
| Trivy                        | Supply chain and filesystem scanning - secrets, CVEs across all ecosystems      |

## Automation

**Pre-commit hooks** (via [pre-commit](https://pre-commit.com)) run a narrow set of checks before
each commit. To install:

```
pre-commit install
```

Only hooks that either fix the problem they detect (`bazel-mod-tidy`, `gazelle`) or prevent unsafe
content from entering the repo (`check-secrets-dir`) run here. Verification-only checks live in the
editor instead (see **Editor integration** below) so they can surface findings without blocking a
commit when you want to switch contexts.

| Hook                | Triggers on                   |
| ------------------- | ----------------------------- |
| `bazel-mod-tidy`    | `go.mod`, `go.work`, `go.sum` |
| `gazelle`           | `*.go` files                  |
| `check-secrets-dir` | files under `secrets/`        |

**Editor integration** (via `.vscode/`) - runs the non-fixing checks on save. Works in VS Code and
VS Code-derived editors (e.g. Google Antigravity). Recommended extensions
(`.vscode/extensions.json`):

- [`golang.go`](https://marketplace.visualstudio.com/items?itemName=golang.go) - runs
  `golangci-lint` on save at package scope, surfacing inline findings that match what CI enforces.
- [`emeraldwalk.runonsave`](https://marketplace.visualstudio.com/items?itemName=emeraldwalk.RunOnSave) -
  triggers the repo-health scripts on save.
- [`ryanluker.vscode-coverage-gutters`](https://marketplace.visualstudio.com/items?itemName=ryanluker.vscode-coverage-gutters) -
  paints gutter marks in Go files from a local `bazel coverage //...` run.

| On-save check        | Triggers on                                |
| -------------------- | ------------------------------------------ |
| `golangci-lint`      | `*.go` files                               |
| `check-go-modules`   | `go.mod`, workflow `.yml`, `.golangci.yml` |
| `check-go-work`      | `go.mod`, `go.work`                        |
| `check-python-scale` | `BUILD.bazel` files                        |

**Viewing coverage locally**: run `bazel coverage //...` from the repo root, then open the
Command Palette and pick _Coverage Gutters: Display Coverage_ (or _Watch_ for live updates).
The merged lcov lives at `bazel-out/_coverage/_coverage_report.dat`; the extension is
pre-configured in [`.vscode/settings.json`](.vscode/settings.json) to find it there. CI uploads
the same file to Codecov, so local gutters and the Codecov dashboard reflect the same data.

**Dependency updates** are managed automatically by [Renovate](https://docs.renovatebot.com), which
groups updates into separate PRs: Bazel toolchains and rulesets, Go dependencies, GitHub Actions,
and language toolchain SDKs (Go and Python version pins in `MODULE.bazel`).
