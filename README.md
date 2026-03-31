# Unnatural Designs

Joshua Yanchar's personal projects monorepo.

## Repository Organization

| Directory | Purpose |
|-----------|---------|
| `//apps/` | End-user-facing applications with persistent UIs (desktop, mobile) |
| `//docs/` | Documentation not tightly coupled to a specific project |
| `//infra/` | Infrastructure-as-code: Terraform, Helm, Pulumi, etc. |
| `//libs/` | Shared libraries consumed by other packages in this repo |
| `//meta/` | Monorepo-level configuration, automation, and tooling (CI/CD, etc.) |
| `//services/` | Long-running processes: background daemons and server-side applications |
| `//tools/` | Developer and operator-facing CLI tools: short-lived, task-focused executables |

## Build System

This repo uses [Bazel](https://bazel.build) with [Bzlmod](https://bazel.build/extern/bzlmod) for dependency management. The Bazel version is pinned in `.bazelversion` and managed via [Bazelisk](https://github.com/bazelbuild/bazelisk).

Go build files are generated and maintained by [Gazelle](https://github.com/bazelbuild/bazel-gazelle). After modifying Go source files, run:

```
bazel run //:gazelle
```

After adding new external Go dependencies, run:

```
bazel run //:gazelle-update-repos
```

### Remote Cache

Builds use [Buildbuddy](https://buildbuddy.io) as a remote cache. CI is configured automatically. For local builds, add the following to `.bazelrc.user` (gitignored):

```
build:remote --remote_header=x-buildbuddy-api-key=YOUR_KEY
```

Then pass `--config=remote` to any Bazel command to use the cache.

## CI

GitHub Actions runs four jobs on every push and pull request to `main`:

| Job | Trigger condition |
|-----|------------------|
| Gazelle check | Always — verifies BUILD files match source |
| go.work check | Always — verifies all Go modules are registered in `go.work` |
| Python scale check | Always — fails if `py_*` target count exceeds threshold (see `meta/scripts/check_python_scale.py`) |
| Build and test | After all checks pass |

## Automation

**Pre-commit hooks** (via [pre-commit](https://pre-commit.com)) run a subset of CI checks locally before each commit. To install:

```
pre-commit install
```

The hooks and their triggers:

| Hook | Triggers on |
|------|------------|
| `gazelle` | `*.go` files |
| `check-go-work` | `go.mod`, `go.work` |
| `check-python-scale` | `BUILD.bazel` files |
| `gazelle-update-repos` | `go.mod`, `go.work`, `go.sum` |

**Dependency updates** are managed automatically by [Renovate](https://docs.renovatebot.com), which groups Bazel, Go, and GitHub Actions updates into separate PRs.
