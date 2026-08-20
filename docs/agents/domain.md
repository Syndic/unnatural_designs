# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the
codebase.

## Before exploring, read these

- **`CONTEXT-MAP.md`** at the repo root: it points at one `CONTEXT.md` per context. Read each one
  relevant to the topic.
- **`docs/adr/`**: system-wide architectural decisions.
- **`<context>/docs/adr/`**: decisions scoped to a single context.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest
creating them upfront. The `/domain-modeling` skill (reached via `/grill-with-docs` and
`/improve-codebase-architecture`) creates them lazily when terms or decisions actually get resolved.

Note that `.claude/CLAUDE.md` is a separate artifact and is not a substitute for a `CONTEXT.md`: it
carries cross-cutting engineering invariants (CI, devcontainer, Renovate plumbing), not the domain
glossary.

## File structure

This repo is multi-context. A context is a Bazel package tree under one of the top-level
directories documented in the README (`//apps/`, `//libs/`, `//services/`, `//tools/`, `//infra/`,
`//meta/`, `//platforms/`), not a directory under `src/`.

```
/
├── CONTEXT-MAP.md                                  ← points at each context's CONTEXT.md
├── docs/adr/                                       ← system-wide decisions
├── meta/
│   ├── CONTEXT.md
│   └── docs/adr/
└── tools/network_infrastructure_maintenance/
    ├── CONTEXT.md
    └── docs/adr/
```

Which directories are contexts, which are scaffolding awaiting one, and which will never be one is
recorded in `CONTEXT-MAP.md`. Add a context's `CONTEXT.md` when that context gains code, and
register it there in the same change.

Markdown docs are not Bazel targets, so adding one needs no `BUILD.bazel` edit. If a test ever reads
one as data, add it to an `exports_files` block in that package.

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a
test name), use the term as defined in the relevant `CONTEXT.md`. Don't drift to synonyms the
glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal: either you're inventing language
the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0007 (event-sourced orders), but worth reopening because…_
