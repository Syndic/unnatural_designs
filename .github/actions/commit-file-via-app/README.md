# commit-file-via-app

Composite action that commits file changes to a branch via the GraphQL `createCommitOnBranch`
mutation, authenticated as a GitHub App installation. Input descriptions and per-step rationale
live in [`action.yml`](action.yml).

## Compatibility contract

This action has consumers outside this repo that reference it as
`Syndic/unnatural_designs/.github/actions/commit-file-via-app@main` — floating on `@main`, because
this repo publishes no tags or releases. A breaking change merged here takes effect in those repos
immediately, with no review gate in between.

The public contract is:

- the input surface: `app-id`, `private-key`, `branch`, `file-paths`, `commit-message`;
- the no-op behavior: files with no working-tree diff are skipped, and if none of the listed files
  changed the action succeeds without committing.

Breaking either requires preserving compatibility or updating all consumers in lockstep.

## Known consumers (all `@main`)

- [`renovate-module-bazel-lock.yml`](../../workflows/renovate-module-bazel-lock.yml) (this repo)
- [`renovate-requirements-lock.yml`](../../workflows/renovate-requirements-lock.yml) (this repo)
- `Syndic/.dotfiles` — `.github/workflows/renovate-devcontainer-lock.yml`

## Self-test

[`commit-file-via-app-selftest.yml`](../../workflows/commit-file-via-app-selftest.yml) exercises
the action end-to-end against a scratch branch on every PR that touches this directory, asserting
each clause of the contract above: a changed file is committed and web-flow signed with the
expected content; no changed files leaves the branch tip untouched; and given several paths of
which only some changed, only the changed ones are committed.
