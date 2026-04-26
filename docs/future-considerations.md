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

## Move Linux Builds from glibc to musl

Linux binaries are currently linked against glibc via the Chromium debian-bullseye sysroots
registered in `MODULE.bazel`. This is the well-paved path: glibc is the default on every
mainstream distro, and prebuilt artifacts (Python wheels, cgo libraries) are most widely
available against it.

The alternative is musl. The motivation to switch would be deployment ergonomics: a musl-linked
binary can be statically linked into a single file and dropped into a `FROM scratch` container,
producing ~5MB images with zero runtime dependencies. glibc binaries need a base image with a
loader and libc (e.g. `gcr.io/distroless/base`, ~20MB) and cannot be cleanly static-linked.

Tradeoffs to weigh when revisiting:

- **cgo packages** (Go) — anything cgo-using would need a musl-compatible build path. Most
  popular cgo libs (sqlite, librdkafka, libpq) build fine against musl, but it's per-library.
- **Python C extensions** — would need `musllinux` wheels for any native-code dependency. Major
  packages (numpy, cryptography) ship them; niche packages may not, forcing source builds.
- **Performance** — musl's allocator is slower than glibc's under heavily-threaded workloads,
  and its DNS resolver is simpler (no parallel A/AAAA). Mostly invisible for app code, can
  matter for high-QPS servers.
- **Sysroot sourcing** — Chromium doesn't publish musl sysroots. Options include extracting one
  from an Alpine container, using `zig cc` (bundles its own musl), or rolling our own.

A reasonable trigger to revisit is the first service that we want to ship as a container image
— at that point the scratch-container payoff becomes concrete instead of theoretical. A mixed
setup (glibc for dev/test, musl for prod container builds) is also viable and is what many
shops settle on.

---

## Multi-Platform Fan-Out Build Target

### Problem

Today, a Bazel invocation builds for exactly one target platform — the value of `--platforms`.
To produce binaries for all three of our supported targets (`linux_x86_64`, `linux_arm64`,
`darwin_arm64`) you must invoke Bazel three times, once per `--config=<platform>` shortcut.

We don't have a single `bazel build //some:release` that fans out to every target. There has
been no concrete need yet — CI runs the matrix in parallel anyway, and ad-hoc builds are
target-specific. The trigger to revisit is the first release artifact that needs to be
multi-arch (most likely a multi-arch container image via `rules_oci`, or a developer-tool
distribution bundle).

### Constraint That Shapes the Design

Mac binaries can only be produced on a Mac host (Apple SDK licensing — Apple does not permit
the SDK to be redistributed off macOS). Linux binaries can be produced on either host via the
hermetic LLVM toolchain + Chromium sysroots already in place.

So a one-button "build everything" command needs to know: "darwin is reachable from this
environment" or "darwin is not." Asking the user to remember which target subset to build is
a usability regression from a single command.

### Why The Obvious Bazel Mechanism Doesn't Work

The first instinct is `target_compatible_with` ("incompatible target skipping"), e.g.:

```python
filegroup(
    name = "release_darwin",
    srcs = [":netbox_audit_darwin_arm64"],
    target_compatible_with = ["@platforms//os:macos"],
)
```

This would silently drop the darwin sub-target on linux. **It does not work for our case.**
`target_compatible_with` is evaluated against the *target* platform, not the host. After a
platform transition flips target to `darwin_arm64`, the constraint always matches — Bazel has
no built-in "this target requires a particular *host*" predicate. (In standard Bazel-think,
exec ≠ host because remote execution decouples them.) What we actually mean is "the Apple SDK
toolchain only registers when the exec platform is mac," which Bazel resolves at
toolchain-resolution time and *errors* on miss, not skips.

Other tempting-but-flawed options:

- **`select()` on `@bazel_tools//src/conditions:darwin`** — works, but the semantics of "host
  config" inside `select()` are subtle and have shifted across Bazel versions. Cursed enough
  to avoid for something this load-bearing.
- **A wrapper script** (`tools/release.sh`, a `just` recipe) that detects `uname` and picks
  the platform list — works, but pushes policy out of Bazel and means you can't drive it from
  IDE / pre-commit / arbitrary `bazel build` invocations.
- **Two named targets** (`:release_linux`, `:release_all`) and document which to use from
  which host — works, but rebrands the problem rather than solving it.

### The Recommended Design: Configurable Platform List via `string_list_flag`

Move the policy ("which platforms get built here") out of Starlark entirely and into the
existing per-environment config layer (`.bazelrc`, `.bazelrc.user`, `:remote_bb`, future
`:remote_macos`). Each environment owns its list. Bazel only ever sees the platforms the user
explicitly asked for, so toolchain resolution can't fail on "darwin from linux."

**The flag.** Define a global string-list flag using `@bazel_skylib`:

```python
# //build/release:flags.bzl  (path is illustrative)
load("@bazel_skylib//rules:common_settings.bzl", "string_list_flag")

string_list_flag(
    name = "release_platforms",
    build_setting_default = [
        "//platforms:linux_x86_64",
        "//platforms:linux_arm64",
    ],
    visibility = ["//visibility:public"],
)
```

Default is the safe baseline (linux pair) — a fresh clone with no `.bazelrc.user` builds the
two linux targets and never attempts darwin.

**The macro / rule.** A `multi_platform_release` rule reads
`ctx.attr._release_platforms[BuildSettingInfo].value` and does a split transition that emits
one sub-target per platform in the list. The aggregator is a `filegroup` (or
DefaultInfo-merging rule) collecting their outputs. Sketch:

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
# .bazelrc — committed default. Linux-only baseline.
build --//build/release:release_platforms=//platforms:linux_x86_64,//platforms:linux_arm64

# :remote_bb — explicit (matches the default; spelled out for clarity).
build:remote_bb --//build/release:release_platforms=//platforms:linux_x86_64,//platforms:linux_arm64

# Future :remote_macos — darwin only (or darwin + linux cross if useful).
# build:remote_macos --//build/release:release_platforms=//platforms:darwin_arm64
```

```
# .bazelrc.user — gitignored, on a developer's mac.
build --//build/release:release_platforms=//platforms:linux_x86_64,//platforms:linux_arm64,//platforms:darwin_arm64
```

Result: `bazel build //some:release` from a Mac builds three binaries; from CI builds two;
from the (future) `:remote_macos` backend builds whatever is listed there. No host detection
in Starlark, no wrapper script, no errors on unreachable toolchains.

### Caveats / Things to Know Before Implementing

1. **`.bazelrc.user` is replacement, not append.** It is `try-import`ed at the end, so if
   `.bazelrc` sets the flag and `.bazelrc.user` sets it again, the user file wins entirely —
   there is no syntax for "append darwin to whatever the default is." Fine in practice (the
   user file lists the full desired set), but document it so people aren't surprised.

2. **The flag label is global and load-bearing.** `--//build/release:release_platforms=...`
   is a bzlmod-canonical label resolved the same from any package. Renaming the package or
   target breaks every bazelrc stanza that references it (including users' personal
   `.bazelrc.user` files we can't see). Pick the path carefully and treat it as a stable API.

3. **`string_list_flag` parsing.** Repeated `--flag=value` invocations replace, not append.
   Comma-separated values in a single setting are the canonical form. This is consistent with
   how it must be used in `.bazelrc`.

4. **The transition must be a `1:N` split transition**, not a regular `1:1` transition. The
   rule needs to declare its outputs as a `cfg = transition(...)` with `outputs` listing the
   per-platform sub-binaries. See rules_go's `go_cross_binary` and rules_oci's
   `multi_platform_image` for working precedents to lift from rather than design from scratch.

5. **`target_compatible_with` is still useful as a backstop.** Even with this design, mark
   the per-platform sub-binary outputs `target_compatible_with = ["@platforms//os:macos"]`
   etc. so that if someone ever lists darwin in their flag from a linux exec environment,
   they get a "skipped: incompatible" message instead of a confusing toolchain-resolution
   error. This is belt-and-braces — the flag is the primary mechanism.

6. **CI does not need this.** CI already builds the matrix as separate jobs (one per
   platform, parallel). The flag's CI value would just be the default. The motivation is
   developer ergonomics + future release-bundle targets, not CI throughput.

### Suggested Trigger and Scope When Revisiting

Land this when the *first* of these arrives:

- A multi-arch container image target (`rules_oci`'s `oci_image_index` wants per-arch images
  as inputs — natural consumer of the fan-out).
- A developer-tool distribution bundle (e.g. a tarball of `netbox_audit` for all three
  arches).
- Repeated complaints about "I have to run three bazel commands to test my change everywhere."

Scope of the implementing PR should be:

1. The `string_list_flag` definition (one BUILD file).
2. The `multi_platform_release` rule + macro (probably in `//build/release/`).
3. The `.bazelrc` defaults (committed) and an updated `.bazelrc.user.example` (if we ever add
   one) showing the mac-host override.
4. README section under "Cross-compilation" explaining the flag and how to override it.
5. One real consumer (don't ship the machinery without a user — pick whichever target above
   triggered the work).

### Prior Art to Reference

- `rules_go`'s `go_cross_binary` — simple platform-transition rule, good shape reference.
- `rules_oci`'s `oci_image_index` and the surrounding `rules_multi_arch` patterns — full
  fan-out-and-bundle example.
- `bazelbuild/examples` `hello-cross` — the same toolchain stack we already use, no fan-out
  but useful for sanity-checking transition shape.
- Bazel docs on user-defined build settings:
  <https://bazel.build/extending/config#user-defined-build-settings>
