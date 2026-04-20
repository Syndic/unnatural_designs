# Project Instructions

## Documentation and test hygiene

Documentation and tests are part of every task, not a separate follow-up step.

Before declaring any task complete:

1. **Tests** — new behaviour needs new tests; changed behaviour needs updated tests. If a method
   signature, flag, config field, or observable output changed, the relevant test files change too.

2. **Docs** — after any change to a CLI flag, environment variable, config schema, output format,
   dependency, or CI/workflow structure, grep for markdown files that reference the affected
   component and update them in the same commit.

3. **Future considerations** — if a doc item in `docs/future-considerations.md` described something
   that has now been done, update or remove that item.
