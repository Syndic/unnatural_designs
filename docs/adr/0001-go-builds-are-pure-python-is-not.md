# Go builds are pure; Python builds are not

Go targets build with cgo disabled (`--@rules_go//go/config:pure` in `.bazelrc`), enforced by
[`meta/scripts/check_no_cgo.py`](../../meta/scripts/check_no_cgo.py) as the `no-cgo-check` CI job.
Python is deliberately held to no analogous policy: wheels with C extensions (`numpy`,
`cryptography`, `pydantic`'s rust core) are allowed. Purity is enforced where it is cheap and buys a
real property, and declined where it is neither.

## Considered options

### Enforce purity for Go — accepted

Pure Go means no LLVM toolchain, no sysroots, no Apple SDK handling, and statically linked Linux
outputs. It is cheap to hold: a `.bazelrc` flag plus a small script. The Go toolchain already ships
every `GOOS`/`GOARCH` pair, so the "any host → any target" property comes free.

An audit at the time of the decision found **zero** cgo uses across the repo's source and full
transitive dependency graph — 1 module, 198 transitive packages. Every direct Go dependency
(`vbauerster/mpb`, `VividCortex/ewma`, `acarl005/stripansi`, `clipperhouse/uax29`,
`mattn/go-runewidth`, `golang.org/x/sys`) is pure Go, and the expected near-term workload
(network-management tooling) has no obvious cgo trigger. Setting up LLVM + sysroot infrastructure
for a hypothetical future need was rejected as premature complexity.

The check rejects any `.go` file containing `import "C"`, and any transitive Go dependency that
compiles C, C++, cgo, SWIG, or ships pre-built `.syso` objects (detected via `go list -deps` with
`CGO_ENABLED=1`).

### Enforce the same for Python — rejected

The same property is much more expensive to maintain and the benefit is smaller:

- **Auto-detection is weak.** Python has no `import "C"` marker. Source-level scans (`import
  ctypes`, `.pyx`, `Extension(...)`) catch the blatant cases but miss the dominant failure mode: an
  innocent-looking `import numpy` whose transitive dependencies ship C. The clean dependency-level
  check — every resolved wheel ends in `-py3-none-any.whl` — needs a `rules_python` pip lock file we
  don't have, and only catches issues after the lock is generated.
- **The ecosystem expects native wheels.** Refusing them closes the door on most numerics, crypto
  and serialization libraries, and the cost compounds with every dependency added.
- **CI matrix coverage is a workable substitute.** If a Python target works on a Linux runner and a
  Mac runner, it works on the platforms we ship to. We don't need "build darwin from a Linux host"
  in the abstract; we need "darwin works on Macs" in practice.

## Consequences

- The "any host → any target" property holds for Go and **not** for Python. Python targets are built
  and tested on a host matching the target platform; CI's per-platform runners cover the supported
  set.
- Introducing cgo later is a deliberate decision with visible cost, not a quiet drift — which is the
  point of enforcing the policy rather than merely stating it.
- Pure Go is why `rules_python_gazelle_plugin` cannot be adopted today: its gazelle binary depends on
  `smacker/go-tree-sitter` via cgo. See #239.
