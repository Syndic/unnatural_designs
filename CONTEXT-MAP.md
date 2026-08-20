# Context Map

This repo is multi-context. A context is a Bazel package tree under one of the top-level
directories in the [README](README.md), not a directory under `src/`. See
[`docs/agents/domain.md`](docs/agents/domain.md) for how the engineering skills consume this file.

Each context owns its own `CONTEXT.md` (its glossary) and its own `docs/adr/` (decisions scoped to
it). Repo-wide decisions live in `docs/adr/`.

## Contexts

- **Network infrastructure maintenance** — `tools/network_infrastructure_maintenance/`
  _(`CONTEXT.md` not yet written)_

  A home network modelled in [NetBox](https://netboxlabs.com/products/netbox/) and controlled by
  [UniFi Network](https://unifi.ui.com/). This is the only context with a domain in the modelling
  sense — devices, cables, IP addressing, VRFs, PoE budgets, DHCP reservations.

  **Built:** `cmd/netbox_audit`, which validates the NetBox model for internal consistency. It is
  the context's only binary.

  **Planned, not built:** drift detection between NetBox's intended state and the live UniFi
  controller, and a NetBox → UniFi sync. The tool's README describes both with their command names
  still elided, so neither has a name yet, let alone code.

  The vocabulary above describes the modelled network, not the set of tools that exist against it.
  Treat a request for anything under "planned" as unimplemented: a redundancy check that matched a
  request against this domain language would otherwise close a real one as already-done.

- **Repo meta** — `meta/`
  _(`CONTEXT.md` not yet written)_

  The monorepo's own automation: `meta/scripts/` (pre-commit checks, changed-path classification,
  Renovate proposal ratification and manual-job triggering, base-image pin sync) and
  `meta/devcontainer-base/` (the shared devcontainer base image, whose README is the canonical home
  for the container plumbing this repo and `Syndic/.dotfiles` both consume).

## Relationships

- **Repo meta → every other context**: build-time and CI-time only. `meta/` operates on the
  repository as an artifact — its files, its lockfiles, its container — and shares no domain
  language with what the code inside those contexts is about. A change in one is not expected to
  move the vocabulary of the other.

## Not yet contexts

`//apps/`, `//libs/`, `//services/`, and `//infra/` are scaffolding: each carries a `BUILD.bazel`
holding nothing but a one-line comment. Add a `CONTEXT.md` under one when it gains code, and
register it above in the same change.

## Not a context

`//platforms/` does hold code — the three `platform()` definitions `.bazelrc` selects with
`--config=linux_x86_64`, `--config=linux_arm64`, and `--config=darwin_arm64` — but it is build
configuration rather than a domain. Three constraint tuples carry no vocabulary to model and no
decisions to record, so it gets no `CONTEXT.md` and is not expected to grow one.
