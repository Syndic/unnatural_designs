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

- the input surface: `client-id`, `private-key`, `branch`, `file-paths`, `commit-message`;
- the no-op behavior: files with no working-tree diff are skipped, and if none of the listed files
  changed the action succeeds without committing.

Breaking either requires preserving compatibility or updating all consumers in lockstep.

## Known consumers (all `@main`)

- [`renovate-derived-files.yml`](../../workflows/renovate-derived-files.yml) (this repo) — commits
  `uv.lock`, `requirements_lock.txt`, and `MODULE.bazel.lock` in a single invocation
- `Syndic/.dotfiles` — `.github/workflows/renovate-devcontainer-lock.yml`

## Self-test

[`commit-file-via-app-selftest.yml`](../../workflows/commit-file-via-app-selftest.yml) exercises
the action end-to-end against a scratch branch on every PR that touches this directory, asserting
each clause of the contract above: a changed file is committed and web-flow signed with the
expected content; no changed files leaves the branch tip untouched; and given several paths of
which only some changed, only the changed ones are committed.

`Action self-test` is the name to require in the `main` ruleset — requiring it is what would make
the exercise a gate rather than a report. **It is not named there yet**
([#312](https://github.com/Syndic/unnatural_designs/issues/312)), so today the self-test reports
without blocking. The workflow is shaped so that the name can be required: it runs on every PR and
classifies the diff inside the job, because a trigger-level `paths:` filter would leave the check
permanently pending on PRs that touch nothing here.

**Changes to this directory cannot come from a fork.** GitHub withholds the app credentials from
fork PRs, so the self-test cannot run there, and a change to the action that nothing exercised is
what this check exists to stop — so the check fails rather than skipping. A fork PR that touches
nothing here passes normally. If you are working from a fork and need a change here, open an issue
and it can be carried on a branch in this repo, where the exercise runs.
