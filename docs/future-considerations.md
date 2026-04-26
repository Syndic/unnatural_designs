# Future Considerations

Items flagged during development as worth revisiting when the time is right.

---

## Local Secret Management

Currently, local Bazel remote cache credentials are stored in `.bazelrc.user` (gitignored). This is
acceptable for a single-user machine but not rigorous. Other secrets are stored in //secrets, which
is gitignored.

A proper solution would retrieve secrets from a secrets manager (e.g. macOS Keychain, 1Password CLI)
rather than storing them in a file on disk.

---

## Renovate Trigger Frequency

Renovate appears to trigger on every branch push rather than only on changes to main. This is more
frequent than expected and worth investigating — it may be a configuration issue or a default
behaviour that can be tightened.

---

## Python Coverage Reporting

Go coverage is now reported via `bazel coverage //...` + Codecov in the CI `coverage` job.
Python coverage is not yet wired in; `coverage.py` output can be merged into the same Codecov
upload once there is meaningful Python code to measure.

---

## Gazelle Python Support

Gazelle has a Python plugin bundled with `rules_python` that can auto-generate `py_library`,
`py_binary`, and `py_test` targets — equivalent to what it does for Go. It was not configured
because the Python footprint is currently small.

This is actively tracked: the `python-scale-check` CI job and pre-commit hook will fail when the
py\_\* target count exceeds the configured threshold, at which point this item should be actioned.

---

## Introducing cgo or Python C-Extensions

The build infrastructure assumes **pure Go** (no cgo) and **pure Python** (no native
C-extensions). This assumption is what lets any host build for any supported target with
no extra setup: Go's own toolchain ships every `GOOS`/`GOARCH` pair, and pure-Python code
is platform-agnostic at the source level. No LLVM toolchain, no sysroots, no Apple SDK
dance. The cross-compile story (see README "Cross-compilation") works directly out of
rules_go in pure mode, configured via `--@rules_go//go/config:pure` in `.bazelrc`.

The assumption is enforced by [`meta/scripts/check_no_cgo.py`](../meta/scripts/check_no_cgo.py),
which runs as the `no-cgo-check` CI job. It rejects:
- Any `.go` file in this repo containing `import "C"`.
- Any transitive Go dependency that compiles C, C++, cgo, SWIG, or ships pre-built
  `.syso` objects (detected via `go list -deps` with `CGO_ENABLED=1`).

There is no equivalent automated check for Python C-extensions today because there are
no pinned Python deps at all (no `pip` extension, no `requirements.txt`). When the first
real Python target lands, the policy should grow a sibling check.

### What Would Have to Change to Allow cgo

If a future need is concrete enough to warrant breaking the policy — e.g. a service
genuinely requires a C library with no pure-Go alternative (`librdkafka`, `libpcap`,
`libssh2`), or a Python target needs `numpy`/`cryptography` — the cross-compile
infrastructure would need to be rebuilt. The work is non-trivial:

1. **Switch `.bazelrc` off pure mode** for the affected targets (or globally) by removing
   `--@rules_go//go/config:pure`, or scope it via per-target `pure = "off"` overrides.
2. **Register a hermetic C/C++ toolchain.** `toolchains_llvm` is the standard choice; it
   downloads a pinned LLVM and uses it for all C/C++ compilation. Needs
   `--incompatible_enable_cc_toolchain_resolution` so platform-based toolchain resolution
   picks it up instead of Bazel's legacy host-cc lookup.
3. **Pin per-target sysroots.** A C compiler is not enough — to produce a `linux_arm64`
   binary on a Mac you need the target OS's libc headers and link libraries. The Chromium
   debian-bullseye sysroots are the de-facto standard; they're the same artifact used by
   the bazelbuild/examples hello-cross sample.
4. **Pin the BuildBuddy executor image** via `exec_properties = {"container-image": ...}`
   on `//platforms:linux_x86_64` so the executor's libc version is known and reproducible.
   Be aware of the LLVM-prebuilt × ubuntu × libtinfo matrix: 18.1.8 only ships an
   ubuntu-18.04 prebuilt (libtinfo5), which won't load on ubuntu-22.04 (libtinfo6).
   17.0.6 has an ubuntu-22.04 prebuilt; that's the workable pin today.
5. **Accept that darwin cross-compile becomes Mac-only.** Apple does not permit the SDK
   to be redistributed off macOS, so producing darwin binaries with cgo dependencies is
   not legally workable from a Linux executor. The current "any host → any target" matrix
   collapses to "Mac → all three; Linux → linux pair only."
6. **For Python C-extensions:** wire up rules_python's pip integration, lock dependencies
   per platform (manylinux + macosx wheels), and accept that wheel availability for less
   popular packages is uneven (especially under musllinux if we ever go that direction).

PR #29 on `worktree-cross-toolchain` (closed without merging in favour of this simpler
design) walked through the full LLVM + sysroot + executor-image setup end-to-end and is
the best reference for what this work looks like in practice.

### Why The Current Choice Was Made

We audited cgo usage at the time of this decision and found **zero** uses across the
repo's source and full transitive dep graph (1 module, 198 transitive packages). Every
direct Go dep (vbauerster/mpb, VividCortex/ewma, acarl005/stripansi, clipperhouse/uax29,
mattn/go-runewidth, golang.org/x/sys) is pure-Go. The expected near-term workload
(network-management tooling) does not have an obvious cgo trigger.

Setting up the LLVM + sysroot infrastructure for a hypothetical future need was rejected
as premature complexity. The policy + enforcement check make the assumption explicit
rather than implicit, so a future change is a deliberate decision rather than a quiet
drift.

---

## Multi-Platform Fan-Out Build Target

### Problem

A Bazel invocation builds for exactly one target platform — the value of `--platforms`.
To produce binaries for all three of our supported targets you must invoke Bazel three
times, once per `--config=<platform>` shortcut.

There is no single `bazel build //some:release` that fans out to every target. There has
been no concrete need yet — CI runs the matrix in parallel anyway. The trigger to
revisit is the first release artifact that needs to be multi-arch (most likely a
multi-arch container image via `rules_oci`, or a developer-tool distribution bundle).

### Constraint That Shapes the Design

While the build infrastructure is pure-Go, all three targets are reachable from any host
(see README "Cross-compilation"). So the fan-out target itself has no per-host
restrictions. **However**, if cgo is ever introduced (see above), darwin becomes
Mac-only and the design needs to gain a host-aware platform list. The design below
anticipates that — the per-environment platform list pattern handles both cases without
re-design.

### The Recommended Design: Configurable Platform List via `string_list_flag`

Move the policy ("which platforms get built here") into the existing per-environment
config layer (`.bazelrc`, `.bazelrc.user`, `:remote_bb`, future `:remote_macos`). Each
environment owns its list. Bazel only ever sees the platforms the user explicitly asked
for, so toolchain resolution can't fail on an unreachable target.

**The flag.** Define a global string-list flag using `@bazel_skylib`:

```python
# //build/release:flags.bzl  (path is illustrative)
load("@bazel_skylib//rules:common_settings.bzl", "string_list_flag")

string_list_flag(
    name = "release_platforms",
    build_setting_default = [
        "//platforms:linux_x86_64",
        "//platforms:linux_arm64",
        "//platforms:darwin_arm64",  # safe today because pure-Go cross works everywhere
    ],
    visibility = ["//visibility:public"],
)
```

(If the policy ever flips to "cgo allowed," change the default to the linux pair only
and let mac users opt darwin in via `.bazelrc.user`.)

**The macro / rule.** A `multi_platform_release` rule reads
`ctx.attr._release_platforms[BuildSettingInfo].value` and does a 1:N split transition
that emits one sub-target per platform in the list. The aggregator is a `filegroup`
collecting their outputs.

```python
multi_platform_release(
    name = "netbox_audit_release",
    binary = "//tools/network_infrastructure_maintenance/cmd/netbox_audit",
)
```

Building `:netbox_audit_release` produces one binary per platform listed in
`--//build/release:release_platforms`.

**The `.bazelrc` wiring.**

```
# .bazelrc — committed default. All three targets, since pure-Go means all are reachable.
build --//build/release:release_platforms=//platforms:linux_x86_64,//platforms:linux_arm64,//platforms:darwin_arm64

# :remote_bb — linux executor, only build linux outputs (skip the round-trip cost for darwin).
build:remote_bb --//build/release:release_platforms=//platforms:linux_x86_64,//platforms:linux_arm64
```

### Caveats Worth Knowing Before Implementing

1. **`.bazelrc.user` is replacement, not append.** It is `try-import`ed at the end, so
   if `.bazelrc` sets the flag and `.bazelrc.user` sets it again, the user file wins
   entirely. Document this.

2. **The flag label is global and load-bearing.** `--//build/release:release_platforms=...`
   is a bzlmod-canonical label resolved the same from any package. Renaming the package
   or target breaks every bazelrc stanza that references it (including users' personal
   `.bazelrc.user` files we can't see). Treat it as a stable API.

3. **The transition must be a `1:N` split transition**, not a regular `1:1` transition.
   The rule needs to declare its outputs as a `cfg = transition(...)` with `outputs`
   listing the per-platform sub-binaries. See `rules_go`'s `go_cross_binary` and
   `rules_oci`'s `multi_platform_image` for working precedents.

4. **CI does not need this.** CI already builds the matrix as separate jobs (one per
   platform, parallel). The motivation is developer ergonomics + future release-bundle
   targets, not CI throughput.

### Suggested Trigger and Scope When Revisiting

Land this when the *first* of these arrives:

- A multi-arch container image target (`rules_oci`'s `oci_image_index` wants per-arch
  images as inputs — natural consumer).
- A developer-tool distribution bundle (e.g. a tarball of `netbox_audit` for all three
  arches).
- Repeated complaints about "I have to run three bazel commands to test my change
  everywhere."

Scope of the implementing PR:

1. The `string_list_flag` definition (one BUILD file).
2. The `multi_platform_release` rule + macro (probably in `//build/release/`).
3. The `.bazelrc` defaults (committed) and an updated `.bazelrc.user.example` (if we
   ever add one) showing per-environment overrides.
4. README section under "Cross-compilation" explaining the flag and how to override it.
5. One real consumer (don't ship the machinery without a user).

### Prior Art to Reference

- `rules_go`'s `go_cross_binary` — simple platform-transition rule, good shape reference.
- `rules_oci`'s `oci_image_index` and the surrounding `rules_multi_arch` patterns.
- Bazel docs on user-defined build settings:
  <https://bazel.build/extending/config#user-defined-build-settings>
