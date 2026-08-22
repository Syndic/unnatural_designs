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
A guard that enforces an invariant no single language toolchain owns. Whether it blocks depends on
where it runs — CI, pre-commit, or on-save in the editor — not on what it is.
_Avoid_: gate, guard, linter, validator

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
