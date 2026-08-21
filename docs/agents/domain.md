# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the
codebase.

## Before exploring, read these

- **`CONTEXT-MAP.md`** at the repo root: it points at one `CONTEXT.md` per context. Read each one
  relevant to the topic.
- **`docs/adr/`**: system-wide architectural decisions.
- **`<context>/docs/adr/`**: decisions scoped to a single context.

**ADR numbers are unique repo-wide, not per directory.** A new ADR takes the next number across
*every* `docs/adr/` directory in the repo, whichever one it lands in — so `docs/adr/0001-…` and
`meta/docs/adr/0002-…` is correct and a second `0001` anywhere is not. This deliberately diverges
from the `/domain-modeling` skill's `ADR-FORMAT.md`, which scans a single directory; follow this
file, and note that a per-directory scan will hand you a number that is already taken. The
directory still says whose decision it is: repo-wide, or one context's.

**To get the next number, ask the check — don't search the repo:**

```
python3 meta/scripts/check_adr_numbers.py --next
```

It prints the number alone (e.g. `0003`) so it can be substituted straight into a filename, and it
answers even while a collision exists, since that is when you most need it. The number is the
highest in use plus one, never a gap left by a deleted ADR — reusing one would break every
reference to the original.

`meta/scripts/check_adr_numbers.py` with no arguments enforces both the uniqueness and the
`NNNN-kebab-slug.md` filename shape, as the `ADR number uniqueness check` CI job. A duplicate is
reported with a free number to move to, so a collision carries its own fix.

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
