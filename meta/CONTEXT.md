# Repo meta

The shared devcontainer base image that Syndic repos build their development environments on, and
the scripts that operate on this repository rather than shipping anything a user runs. Terms about
the repository itself — checks, derived files, marker comments — are in the root
[`CONTEXT.md`](../CONTEXT.md).

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
