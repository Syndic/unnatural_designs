# The plumbing contract includes the host-presentation shape

The shared devcontainer base image's contract with a consumer is not merely "call the dispatcher".
It includes the shape the host stub must present: fixed file names under `.git-plumbing/`, fixed
symlink names beside it, and the destinations the plumbing writes to. A consumer conforms to that
layout. What stays free is where on the host each value is read from, which is what the symlink
indirection buys.

## Considered options

### The steps only — rejected

Under this reading the image publishes behaviour, and each consumer presents host state however it
likes. It is what `meta/devcontainer-base/README.md` implied by saying the stub "does not constrain
where the host keeps anything".

The code never matched it. `plumbing_apply_git_common` reads `$PLUMBING_DIR/host-git-common-path`
by name and links it to `/host-git-common`; `plumbing_apply_all` reads `host-timezone` by name.
Those are contract terms whether or not they are written down as such. Leaving them undocumented
means the next consumer discovers them by breakage rather than by reading, and `Syndic/.dotfiles`
demonstrates the cost: it presents `known_hosts` and `allowed_signers` as snapshot files where this
repo presents them as bind-mounted symlinks, so its copy of the plumbing diverged in kind rather
than in detail.

### The steps plus the shape — accepted

The contract names the files, the symlinks and the destinations, and the README documents them as
an interface. The host stub's remaining freedom is real but bounded: it chooses what each symlink
points at, so a developer's `known_hosts` can live anywhere, but it does not choose the name the
container looks for.

## Consequences

- Adoption is conformance, not integration. `Syndic/.dotfiles` moves to the documented shape rather
  than keeping its own; its snapshot-based handling is an artefact of age, not a variant to support.
- A consumer that wants a different layout has one supported answer — change the symlink target —
  and one unsupported one. That is deliberate: configurability on this axis would reintroduce the
  divergence the shared image exists to remove.
- The contract is versioned by the image digest like everything else, so widening it is a base
  image change that reaches consumers on their next bump.
- What the image cannot carry stays outside the contract and unsolved: the host stub itself, the
  two bind mounts, and `containerEnv`. Tracked in #248.
