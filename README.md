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

### Remote Cache

Builds use [Buildbuddy](https://buildbuddy.io) as a remote cache. CI is configured automatically.
For local builds, add the following to `.bazelrc.user` (gitignored):

```
build:remote --remote_header=x-buildbuddy-api-key=YOUR_KEY
```

Then pass `--config=remote` to any Bazel command to use the cache.

## CI

Two GitHub Actions workflows run on every push and pull request to `main`.

**CI** — code-change-driven checks:

| Job                           | Trigger condition                                                                                   |
| ----------------------------- | --------------------------------------------------------------------------------------------------- |
| Gazelle check                 | Always — verifies BUILD files match source                                                          |
| go.work check                 | Always — verifies all Go modules are registered in `go.work`                                        |
| Python scale check            | Always — fails if `py_*` target count exceeds threshold (see `meta/scripts/check_python_scale.py`) |
| Go module completeness check  | Always — verifies every Go module is in the workflow matrices and has linter config                 |
| golangci-lint                 | After module check passes — runs per Go module                                                      |
| Build and test                | After all structural checks pass                                                                    |

**Security** — also runs on a weekly schedule (Mondays at 02:00 UTC):

| Job                           | Purpose                                                                          |
| ----------------------------- | -------------------------------------------------------------------------------- |
| Go module completeness check  | Gate for the per-module security jobs below                                      |
| Semgrep                       | SAST — scans for injection flaws, insecure API usage, and hardcoded secrets      |
| govulncheck                   | Dependency CVE scanning — checks reachable call paths against the Go vuln DB     |
| Trivy                         | Supply chain and filesystem scanning — secrets, CVEs across all ecosystems       |

## Automation

**Pre-commit hooks** (via [pre-commit](https://pre-commit.com)) run a subset of CI checks locally
before each commit. To install:

```
pre-commit install
```

The hooks and their triggers:

| Hook                 | Triggers on                                              |
| -------------------- | -------------------------------------------------------- |
| `bazel-mod-tidy`     | `go.mod`, `go.work`, `go.sum`                            |
| `check-go-modules`   | `go.mod`, workflow `.yml` files, `.golangci.yml`         |
| `gazelle`            | `*.go` files                                             |
| `check-go-work`      | `go.mod`, `go.work`                                      |
| `check-secrets-dir`  | files under `secrets/`                                   |
| `check-python-scale` | `BUILD.bazel` files                                      |

**Dependency updates** are managed automatically by [Renovate](https://docs.renovatebot.com), which
groups updates into separate PRs: Bazel toolchains and rulesets, Go dependencies, GitHub Actions,
and language toolchain SDKs (Go and Python version pins in `MODULE.bazel`).
