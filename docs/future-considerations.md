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

## Gazelle Python Support

Gazelle has a Python plugin bundled with `rules_python` that can auto-generate `py_library`,
`py_binary`, and `py_test` targets — equivalent to what it does for Go. It was not configured
because the Python footprint is currently small.

This is actively tracked: the `python-scale-check` CI job and pre-commit hook will fail when the
py\_\* target count exceeds the configured threshold, at which point this item should be actioned.

---

## Introducing cgo or Python C-Extensions

The build infrastructure assumes **pure Go** (no cgo) and **pure Python** (no native
C-extensions). The rationale is build simplicity and hermeticity: pure-Go means no LLVM
toolchain, no sysroots, no Apple SDK handling, and Linux outputs are statically linked.
`rules_go` runs in pure mode via `--@rules_go//go/config:pure` in `.bazelrc`.

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
`libssh2`), or a Python target needs `numpy`/`cryptography` — a hermetic C/C++
toolchain story would need to be built. The work is non-trivial:

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
5. **Accept that darwin builds become Mac-only.** Apple does not permit the SDK to be
   redistributed off macOS, so producing darwin binaries with cgo dependencies is not
   legally workable from a Linux executor — darwin targets must be built on a Mac.
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

## Pure-Python Enforcement (Known Gap)

We protect against cgo via `meta/scripts/check_no_cgo.py`, but **we do not currently
have an analogous check for impure Python** — Python dependencies that ship native code
(C extensions, Cython, etc.). This is a deliberate gap, not an oversight.

### Why The Gap Exists

The cgo check is cheap and authoritative: `go list -deps` is a toolchain-native query
that surfaces native code in any package, and it could run against an existing 198-package
dep graph the moment it was written. The Python equivalent is messier:

- **Source-level scan is much weaker.** Python has no `import "C"` marker. The
  user-visible idioms (`import ctypes`, Cython `.pyx`, `setup.py` `Extension(...)`) catch
  the most blatant cases but miss the dominant failure mode: an innocent-looking
  `import numpy` whose dependency is itself impure.
- **Dependency-level scan needs an artifact we don't have.** The clean check is "every
  resolved wheel in the lock file ends in `-py3-none-any.whl`," but there is no lock
  file yet — `rules_python` is registered and the interpreter is pinned (3.13), but no
  `pip` extension is configured and no Python deps are pinned anywhere.
- **Building the check against zero deps means designing against a hypothetical
  lock-file format with nothing to validate it on.**

### Failure Modes Without The Check

If someone introduces an impure Python dep tomorrow:

- **Most likely first failure: build-time, reasonably clear.** `rules_python` requires
  per-platform lock entries to resolve native wheels. A build for a target without a
  lock yields something like *"no matching wheel found for platform linux_arm64"* —
  names the dep, names the platform, points the contributor toward the pure-vs-impure
  distinction.
- **Less likely but more painful: silent platform mismatch.** If locks are configured
  for every platform and the wheels happen to install everywhere, the build succeeds.
  The cost shows up later as larger artifacts, slower CI, and target-specific runtime
  failures only on platforms that go untested. This is the case the check would catch
  that build errors would not.

### Plan: Build The Check When The First Impure Dep Lands

Test-driven design for build infrastructure: write the check that would have caught the
actual problem, not the check we imagine in advance. The first impure Python dep should
be both:

1. A trigger to add the check that would have flagged it, sized to the actual lock-file
   format `rules_python` produces by then.
2. A deliberate decision — not slipped in unnoticed because no check existed.

The natural trigger to revisit aligns with the existing
**Gazelle Python Support** entry: once `python-scale-check` starts firing because real
Python targets are arriving, we will also have the lock file the check needs.

### Sketch of The Eventual Check

When implemented, the check is roughly:

```python
# meta/scripts/check_pure_python.py
# Walks the rules_python lock file and asserts every resolved wheel filename matches
# the pure-Python pattern: *-py3-none-any.whl. Fails on any platform-specific wheel
# (e.g. *-cp311-cp311-manylinux_2_17_x86_64.whl).
```

Wired into CI alongside `no-cgo-check`. When this lands, the README pure-Go section
should grow a sibling note: "Adding a Python C-extension would require introducing
per-platform native-wheel resolution — `rules_python`'s `pip.parse` with
`requirements_lock_<platform>` locks for every supported target, plus the realistic
risk of wheel-availability gaps on niche platforms (musllinux, linux_arm64). See
future-considerations."

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

Cross-host build reachability is **not** a guaranteed property of the repo — it is a
side effect of the current pure-Go policy and may not survive future toolchain changes.
The design therefore should not assume "any host → any target." Instead it gives each
environment its own platform list, so a Mac dev box, a Linux CI executor, and any
future host-restricted setup each declare exactly which targets they build. This also
handles the cgo case (where darwin would become Mac-only) without re-design.

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
        "//platforms:darwin_arm64",  # works today under the pure-Go policy
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
# .bazelrc — committed default. All three targets (pure-Go policy makes all reachable today).
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

4. **CI handles multi-platform via a runner matrix, not via this rule.** CI runs the
   `build-and-test` job once per supported target on a matching-host runner (see
   `.github/workflows/ci.yml`). That catches per-platform regressions early and runs
   tests natively on each platform. The motivation for an in-Bazel fan-out target is
   different: developer ergonomics (one local command instead of three) and future
   release-bundle artifacts (e.g. a multi-arch container image whose inputs are
   per-platform binaries from one build invocation).

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
4. README section under "Pure-Go policy" explaining the flag and how to override it.
5. One real consumer (don't ship the machinery without a user).

### Prior Art to Reference

- `rules_go`'s `go_cross_binary` — simple platform-transition rule, good shape reference.
- `rules_oci`'s `oci_image_index` and the surrounding `rules_multi_arch` patterns.
- Bazel docs on user-defined build settings:
  <https://bazel.build/extending/config#user-defined-build-settings>
