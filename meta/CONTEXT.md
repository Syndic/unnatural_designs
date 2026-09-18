# Repo meta

The monorepo's own automation: the repo-health checks that enforce cross-cutting invariants, and
the shared devcontainer base image that Syndic repos build their development environments on. It
operates on the other contexts rather than shipping anything a user runs.

## Language

### Devcontainer base image

**Base image**:
The devcontainer image this context assembles and publishes for Syndic repos to build on. It
carries the container half of the plumbing and nothing else — no language toolchains, no features.
_Avoid_: shared image, common image

**Consumer**:
A repo whose devcontainer Dockerfile `FROM`s the base image. This repo is one; `Syndic/.dotfiles`
is the other.
_Avoid_: client, downstream, dependent

**Plumbing**:
The bridging of host state into a container — the git common directory, the host timezone, the
material a signed commit needs. Not git's plumbing/porcelain sense: nothing here is a low-level git
command.
_Avoid_: bootstrap, host integration

**Host stub**:
The `initializeCommand` script a consumer keeps, which runs on the developer's host before any
container exists. Its job is to present host state in the shape the container consumes, without
dictating where the host keeps anything.
_Avoid_: initialize script, host script

**Dispatcher**:
The single command a consumer's lifecycle hooks call to apply every shared plumbing step for a
phase. Being one command rather than a library is what lets a new step reach every consumer on its
next base image bump, with no edit on their side.

**Contract**:
The interface between a consumer's host stub and the base image: the file names, symlink names and
values the stub must present, plus the destinations the plumbing writes to. Conforming to it is
what adoption means.

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
