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

This repo uses [Bazel](https://bazel.build) with [Bzlmod](https://bazel.build/external/bzlmod) for dependency management. The Bazel version is pinned in `.bazelversion` and managed via [Bazelisk](https://github.com/bazelbuild/bazelisk).

Go build files are generated and maintained by [Gazelle](https://github.com/bazelbuild/bazel-gazelle). After modifying Go source files, run:

```
bazel run //:gazelle
```

After adding new external Go dependencies, run:

```
bazel run //:gazelle-update-repos
```

## Automation

**Pre-commit hooks** (via [pre-commit](https://pre-commit.com)) enforce that Gazelle-generated files are kept in sync. To install:

```
pre-commit install
```

**Dependency updates** are managed automatically by [Renovate](https://docs.renovatebot.com), which groups Bazel and Go dependency updates separately.
