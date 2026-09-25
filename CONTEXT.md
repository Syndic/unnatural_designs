# Repository

Vocabulary for the repository as a whole. [`CONTEXT-MAP.md`](CONTEXT-MAP.md) lists the contexts,
each of which keeps its own terms in its own `CONTEXT.md`, and states which glossary a term belongs
in.

## Language

### Repo health

**Check**:
Logic that verifies an invariant about the repository, and has no side effects. A check is
_blocking_ or _non-blocking_; blocking is much the commoner, so an unqualified "check" means a
blocking one, and "blocking check" is the explicit form where the contrast needs saying.

Blocking is relative to the gate of the context the check runs in: a pre-commit hook blocks the
commit, a CI job blocks the merge. What differs between those contexts is not whether a check can
block but whether it can be side-stepped — `--no-verify` skips a hook, and nothing skips a
required status check. So a check may live in a hook or on save in the editor for the fast
feedback, but it must also run in CI, the only place enforcement does not rely on good faith.
`check_secrets_dir.py` is the same invariant in two contexts: the `check-secrets-dir` hook and the
`Secrets check` CI job.
_Avoid_: gate, guard, linter, validator

**Advisory check**:
A check deliberately not named in the ruleset, so its failure does not block a merge. Its verdict
is a judgement call rather than a defect. The distinction is recorded in
`meta/scripts/ci_enforcement_manifest.py`, because a check that gates nothing by decision and one
that gates nothing by omission are indistinguishable from the workflow.
_Avoid_: soft check, warning, non-required check

**Automated task**:
A job that acts on the repo's behalf through side effects. Six of the seven pre-commit hooks are
automated tasks — they rewrite files that don't satisfy project standards — as are
`Publish the shared base image`, `Re-derive lock files` and `Request a Renovate run`. Automated
Tasks may be required or advisory. Blocking or non-blocking.
_Avoid_: action, automation, fixer

**Fan-in**:
The single non-matrix job that aggregates every row of a matrix job, and the only name a ruleset
can require on that matrix's behalf — a row's own check name carries the row that produced it, so
it moves with the matrix. A matrix job with no fan-in is a check nothing can require, and nothing
about it looks wrong.
_Avoid_: aggregator, rollup, gate job

**Derived file**:
A checked-in file that is reproducible from other checked-in sources, so it is regenerated rather
than hand-edited and its staleness is a defect a check can find. Distinct from a dependency, whose
value comes from outside the repo and is bumped rather than derived — the same artifact can be one
in this repo and the other in a consumer.
_Avoid_: generated file, lockfile

**Marker comment**:
A comment that tells Renovate which version string a line carries, where the file's own syntax
can't. A marker no pattern claims is invisible rather than broken, which is the failure mode it is
prone to.
