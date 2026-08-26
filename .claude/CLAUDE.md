# Project Instructions

## Run all tooling in the devcontainer

Every build, test, linter, formatter, code generator, language tool, and `git commit` runs via
`devcontainer exec --workspace-folder . <cmd>`. The host is only for editing files, read-side `git`
(`status`/`log`/`diff`/`rev-parse`/`branch`), and `gh`.

Spin the container up on the first task that could run in it, independent of any cost assessment —
**treat spin-up as free.** This is a standing user preference, not a trade-off to weigh: the user
wants the container up. Bring it up once, reuse it for everything after:

```
devcontainer up --workspace-folder .          # once, at first tool need
devcontainer exec --workspace-folder . <cmd>  # every tool invocation after
```

A missing host tool is a signal to use the container, never to provision it on the host: no
`pip`/`brew`/`go install`, no `uvx`/`npx` to dodge the container (host-version drift, and it won't
satisfy the `language: system` pre-commit hooks anyway). Commits especially — hooks resolve tools
from PATH and signing needs the bridged SSH agent; see the ".devcontainer signed commits" section.

## Documentation and test hygiene

Documentation and tests are part of every task, not a separate follow-up step.

Before declaring any task complete:

1. **Tests** — new behaviour needs new tests; changed behaviour needs updated tests. If a method
   signature, flag, config field, or observable output changed, the relevant test files change too.

2. **Docs** — after any change to a CLI flag, environment variable, config schema, output format,
   dependency, or CI/workflow structure, grep for markdown files that reference the affected
   component and update them in the same commit.

3. **Tracker items** — if an open issue described something that has now been done, close it with a
   comment pointing at the change. If the condition under a `blocked` issue's
   `## Trigger to revisit` heading has fired, say so on the issue rather than silently acting on it.

## Comment style — keep it tight, trim what's oversized

Default to short, single-sentence inline comments that name the non-obvious WHY at the line they
describe. Don't write rationale-prose blocks inline; the home for architectural or cross-cutting
rationale is this CLAUDE.md (see "Superseding CI runs" below as the model — overview here, terse
pointers at the sites). When a fact spans repos rather than just files, the canonical home moves
out to the shared artifact and this file becomes one of the pointers — see ".devcontainer plumbing
and feature pins", whose prose lives in `meta/devcontainer-base/README.md` so `Syndic/.dotfiles`
reads the same copy.

When you touch a file whose existing comments feel oversized for the rent they pay, tightening them
as part of the change is welcome and doesn't need a separate task. Leave the load-bearing facts;
cut the prose around them. If you're unsure whether a comment is load-bearing, surface the proposed
cut before applying it.

## Reminder tags — use only the documented set

Reminder tags (`TODO`, `FIXME`, `HACK`, `NOTE`, `TEND(<task-type>)`) are a closed, greppable
vocabulary defined in `docs/reminder-tags.md`. Don't invent new ones — an ad-hoc tag like
`TRANSIENT:` or `DEFERRED:` is invisible to the per-category grep the system exists for. Pick the
closest documented tag (context/rationale → `NOTE`; correct-now-but-will-change → `TEND`); if none
fits, add the tag to `docs/reminder-tags.md` first, then use it. Watch for accidental tag shapes
too: a leading `Word:` in a comment reads like a tag even when it's just prose — reword it (e.g. to
a parenthetical) so a tag sweep doesn't trip on it.

## commit-file-via-app is a public contract

`.github/actions/commit-file-via-app/` has external consumers referencing it at `@main` — read
[its README](../.github/actions/commit-file-via-app/README.md) before changing its inputs or its
no-op-when-no-diff behavior.

## Which workflow a check belongs in

`ci.yml` and `security.yml` are split on **what makes a check's result change**, not on subject
matter:

- **A check that reads only the tree belongs in `ci.yml`.** Same commit, same answer, forever — so
  re-running it tells you nothing you did not already know.
- **A check whose verdict moves with an input we don't pin belongs in `security.yml`.** Four fetch
  a database at run time — govulncheck the Go vuln DB, pip-audit PyPI advisories, Semgrep the
  registry rule packs, Trivy its own DB — so an advisory landing turns them red on an untouched
  commit. `codeql` qualifies by a different route: the SHA pin fixes the *action*, not the analysis
  engine, which GitHub selects per run (see "CodeQL runs as advanced setup"). Either way the Monday
  cron is what surfaces it.

Read the criterion as "does anything reach this check from outside the tree", not as "is there an
advisory database" — the narrower reading misses CodeQL, and a pinned `uses:` is not evidence that
a check is tree-only.

That axis is why `golangci-lint` moved out of Security in #18, and why `modules-check` later
followed it out — a completeness gate over hand-listed matrices is a pure function of the tree, so
its one run in `ci.yml` is the whole of it. Note `check_modules.py` globs *every* file in
`.github/workflows/`, so that single run validates `security.yml`'s own `govulncheck` matrix and
names it by path.

The temptation the split has to survive is a tree-only gate re-added to `security.yml` so that
workflow's green is self-certifying. It buys nothing: on a PR both workflows run and `ci.yml` blocks
the merge through `build-and-test-all`, and on the schedule the tree being scanned is one that
already passed the gate at merge. What it costs is a job that re-derives a settled answer every
Monday. The accepted consequence is that a stale matrix shows `ci.yml` red while `security.yml`
reports green — the merge is still blocked, but security.yml's green answers a narrower question
than it looks like it does.

## Superseding CI runs

Every workflow except `renovate-derived-files.yml` carries the same block, and the shape of the
group key is the load-bearing part:

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.run_id }}
  cancel-in-progress: true
```

- **PRs group by number**, so a fast-follow push cancels the previous run. The motivating case is a
  Renovate PR: the helper's derived-files commit lands ~30s after Renovate's push, so the first
  devcontainer build spent ~5 of its ~5.5 minutes building a tree — bumped manifest, un-regenerated
  lock — that never merges. Required status checks are evaluated against the PR's head SHA, so
  cancelling a superseded SHA's run never leaves a requirement unmet.
- **Everything else keys on `run_id`**, which is unique per run, so non-PR runs are always alone in
  their group: nothing cancels them and — the subtler half — nothing *queues* behind them either.
  A shared non-PR group would have made a push to main wait on an in-progress weekly security scan
  (`cancel-in-progress: false` semantics), and a third run in that group would be dropped outright.
  Keeping them ungrouped matters because those runs produce artifacts nothing else will: the GHCR
  devcontainer build cache (`push: filter`), the Codecov upload, and the main-branch security
  baseline.

`renovate-derived-files.yml` is the deliberate exception; the reason is at that file's own
`concurrency` note, since it is a property of that workflow rather than of the convention.

## CodeQL runs as advanced setup

The `codeql` job in `security.yml` replaced GitHub's **default setup** — the managed
configuration that runs CodeQL with no workflow file in the repo. Load-bearing facts:

- **Why the switch.** Default setup analyses Go with autobuild under the runner image's own Go
  and `GOTOOLCHAIN=local`, and offers nowhere to put a step, so nothing could point it at
  `go.work`. The first bump past the image's version therefore failed extraction outright:
  Renovate's Go 1.27.0 PR (#233) died on `go.work requires go >= 1.27.0 (running go 1.26.6)`
  while every other Go job passed, because those resolve the toolchain from `setup-go`'s
  `go-version-file: go.work`. A workflow can run that step ahead of `codeql-action/init`; the
  managed configuration cannot. `//meta/scripts:test_codeql_toolchain` keeps the step and its
  position.
- **The two setups are mutually exclusive, and default setup wins.** Re-enabling it (Settings →
  Advanced Security → CodeQL analysis) does not merge with the workflow, it displaces it: the
  job still runs, and its upload is rejected with "Upload was rejected because CodeQL default
  setup is enabled". So the settings flip comes *before* the workflow lands, not after — in the
  other order every PR run fails on the upload step.
- **The job reproduces what default setup had configured**: languages `actions`, `go`, `python`;
  the `default` query suite and `remote` threat model (both are codeql-action defaults, so
  neither is spelled out); `ubuntu-latest`; categories `/language:<lang>`. The weekly cadence is
  now `security.yml`'s own Monday cron rather than a separate schedule.
- **The SHA pin does not pin the analysis, and nothing here should.** With no `tools:` input,
  `codeql-action/init` resolves the CLI — and the query packs in the bundle it carries — per run
  from GitHub's `default_codeql_version_*_enabled` feature flags; `defaults.json` is the
  *override*, reached only via `tools: linked` (the action's own `getCodeQLSource` comment says it
  in those words). So a bundle GitHub rolls out server-side brings new default-suite queries to an
  untouched commit, which is what earns this job its place on the cron. Leave `tools:` unset:
  `tools: linked` is the one input that would pin the analysis to the action release and strand it
  on older queries. The `uses:` pin itself is unrelated and stays — it is repo-wide policy
  (README's "Dependency updates"), it guards the code running with `security-events: write` and
  `autobuild` over our source, and Renovate keeps it current in the routine batch.
- **The bundle's Go extractor is built against a Go release, and can lag the one this repo
  pins.** Since 2026-08-20 — #236 giving Go analysis a toolchain and #233 moving the pin to
  1.27.0, 70 minutes apart — the autobuilder has logged `Autobuilder was built with go1.26.5,
  environment has go1.27.0` and given up on five standard-library packages it could not
  type-check: `internal/poll`, `math/rand/v2`, and the vendored `x/net/idna`,
  `x/text/unicode/bidi` and `x/text/unicode/norm`. Those are I/O, network and text-normalisation
  primitives, i.e. what taint tracking wants to follow, so the analysis covers less than it looks
  like it does. It clears on GitHub's schedule for the same reason the previous bullet gives, and
  `tools:` would pin an *older* extractor rather than a newer one.
- **`meta/scripts/codeql_extraction_report.py` is what says so**, run after `analyze` on every
  matrix row. CodeQL calls a run that read part of the code a success and GitHub raises no
  annotation for the failures, so before this the only record was `##[error]` lines inside a green
  job's raw log — 55 runs of them in one week before anyone read the logs. The report reads the
  diagnostics out of the SARIF the CLI wrote, since the uploaded copy has them stripped, and
  re-emits them as a warning annotation and a step summary. It fails the job for one case only:
  an extraction failure *inside* the source root, which is this repo's own code going unanalysed
  and never waits on an upstream release. Failures outside it warn and stay green — a hard gate
  there would block every merge from the next Go bump until GitHub shipped a new bundle, an
  outage on a schedule nobody here controls. Missing diagnostics warn too, and deliberately do not
  read as clean: the CodeQL Action includes them based on a feature flag it resolves from GitHub
  per run, so "no diagnostics" is not "no problems". There is no tolerance count in any of the
  three, so there is nothing to widen the day a run goes red.
- **`build-mode: none` is not available for Go** (nor Swift or Kotlin), so Go is the one language
  whose analysis has to build, and so the one that needs a toolchain on PATH.
- **`CodeQL Analysis (all languages)` is the name for the ruleset to require**, not the
  per-language jobs — those are matrix rows, so their check names move with the language list. The
  `codeql-all` fan-in gives branch protection one stable name and makes every row required through
  it, so an added language needs no ruleset edit. Its `if: always()` is what makes that real:
  without it a failed matrix *skips* the fan-in, and branch protection counts a skipped required
  check as passed. The context itself is repo settings and unreadable from here, so
  `//meta/scripts:test_codeql_toolchain` holds the workflow and the docs that quote it to the one
  string.

## Renovate auto-commit helper (`Renovate helper` app)

`.github/workflows/renovate-derived-files.yml` regenerates the derived files Renovate can't
(Mend-hosted Renovate can't run `uv lock`, `bazel mod deps`, or `go mod tidy`/`go work sync` —
the `allowedUnsafeExecutions` gate) and commits them back to the PR via a dedicated GitHub App,
the `Renovate helper`. Load-bearing facts:

- **Why an app, not `GITHUB_TOKEN`.** The commit goes through the `commit-file-via-app` action
  (see the section above), whose `createCommitOnBranch` mutation is web-flow-signed — satisfying
  branch protection — and is attributed to the app, so the push retriggers required status checks.
  A `GITHUB_TOKEN`-authored push suppresses downstream check runs; don't switch to it.
- **uv before Bazel, one commit.** `pip.parse` reads `requirements_lock.txt`, and the artifact
  hashes it resolves are recorded in `MODULE.bazel.lock`'s `facts`. So the workflow settles
  `requirements_lock.txt` (uv) before regenerating `MODULE.bazel.lock` (`bazel mod deps`), and
  commits both in one mutation — two separate workflows couldn't order the steps and would race two
  mutations on `expectedHeadOid`. A Python PR triggers the Bazel refresh only when it actually moves
  `requirements_lock.txt` from the merge base (a pyproject-only edit that re-resolves the same is a
  no-op). The Bazel refresh only rewrites the pip `facts` on a cold Bazel output base, so the
  workflow's `setup-bazel` sets only `bazelisk-cache`/`repository-cache` — adding `disk-cache` would
  make it a silent no-op.
- **`.bazelversion` invalidates the lock too.** `MODULE.bazel.lock`'s `lockFileVersion` (and the
  shape of its recorded extensions) tracks the bazel release, so a Renovate bazel bump leaves the
  committed lock stale. Builds don't notice — `--lockfile_mode=update` rewrites in memory and stays
  green — so the staleness surfaces either as a blocked local commit (`base-image-pin` selects
  `.bazelversion` itself and rewrites the lock as a side effect of its `bazel build`;
  `bazel mod tidy` does the same, but only on a `go.mod`/`go.work`/`go.sum` change) or as ci.yml's
  `MODULE.bazel.lock freshness` job, which is the backstop for commits that ran no hook. The
  workflow therefore triggers on `.bazelversion` and folds it into the same `bazel` classification
  as `MODULE.bazel`: one output, one `bazel mod deps` refresh. bazelisk reads the checked-out
  `.bazelversion`, so the regenerated lock is in the bumped version's format.
- **Go tidy/sync rides the same commit.** Renovate's `go get` bumps `go.mod`/`go.sum` but never
  runs `go mod tidy` (opt-in) or `go work sync` (Renovate does it only when vendoring, which this
  repo doesn't) — so the indirect block and `go.work.sum` are left stale. The workflow runs
  `go mod tidy` in each `go.work` member, then `go work sync`, and adds the touched go.mod/go.sum
  plus `go.work.sum` to the single commit. Unlike uv→Bazel, Go is *independent* of the other two
  (no ordering constraint); it shares the job purely so a grouped Go+Python+Bazel PR settles in one
  `expectedHeadOid` mutation instead of racing. The tidy/sync target lists come from
  `meta/scripts/go_derived_files.py` (`--what modules|files`), which reads `go.work`'s `use`
  directives — so a module added to the workspace is picked up with no workflow edit. Unlike the
  uv path, there's no "conflict" review: a bump `go mod tidy` can't settle just fails the job.
  MODULE.bazel.lock is *not* in this set — gazelle's `go_deps` extension is reproducible and absent
  from the lockfile, and `use_repo` tracks only direct imports (unchanged by a version bump).
- **The devcontainer base-image pin rides it too.** `.devcontainer/Dockerfile`'s `FROM` digest
  is derived from `//meta/devcontainer-base:image`, which is assembled over the
  `devcontainers_base_debian` pull — so a `MODULE.bazel` bump restales it. The workflow rebuilds
  the image and rewrites the pin (`meta/scripts/sync_base_image_pin.py`) *after* `bazel mod deps`,
  never before: that step needs a cold output base, and the build would warm it. Runs
  `--config=local`, since this job carries no BuildBuddy key.
- **Devcontainer feature lock rides it too.** `devcontainer upgrade` reruns when
  `devcontainer.json` moves. Like Go, it is *independent* of the uv→Bazel ordering and shares the
  job only so a grouped PR settles in one `expectedHeadOid` mutation. Needs no Docker (OCI metadata
  only), so the cost is an npm install of the CLI. The lock's derived-file contract, and why the
  references must be full semver and carry no digests, live in the devcontainer plumbing section.
- **App permissions.** `Contents: read & write` (commit) and `Pull requests: read & write` (to file
  and dismiss the `REQUEST_CHANGES` reviews the ratify step raises on an unresolvable bump). Set in
  the app's GitHub settings; no code.
- **`gitIgnoredAuthors` is an exact-string match.** `renovate.json` lists the helper bot's
  commit-author email so Renovate doesn't treat the auto-commit as a user edit — which would
  suppress its own follow-up rebases/bumps on that branch. Renovate matches by exact string (a
  `Set.delete`, no wildcard); the email is `<numeric-id>+<app-slug>[bot]@users.noreply.github.com`,
  and both halves change if the app is recreated.

**If the app is recreated:** wait for the next Renovate PR where the auto-commit fires, read the new
author email
(`gh api repos/Syndic/unnatural_designs/pulls/<n>/commits --jq '.[].commit.author.email'`), update
`gitIgnoredAuthors` in `renovate.json`, and re-grant the two permissions above. Until that lands
Renovate reacts to the helper's commits as user edits — visible, not load-bearing.

## Renovate run after automerge

`.github/workflows/renovate-run-after-automerge.yml` exists because **a merge attributed to
`renovate[bot]` does not produce a Renovate job.** When Renovate automerges one of its own PRs,
nothing tells it that `main` moved, so every other open Renovate PR sits `BEHIND` — and this
repo's ruleset requires up-to-date branches, so those PRs are unmergeable — until the next
scheduled run.

The observations behind that, from the Mend job log (developer.mend.io) and the repo's own event
history:

- Every push to `main` by a human that left a Renovate branch behind was followed by a
  `requested` job within 0.4–5 minutes. The one push authored by `renovate[bot]` (PR #221,
  2026-07-31 08:32:28Z) produced **no job at all**; PR #216 was rebased 6h25m later by a job whose
  `Reason` was blank, i.e. the schedule.
- Not a rate limit: eleven human merges landed *closer* behind a previous run than that one did
  (one at 0.0 minutes) and still triggered within ~1 minute.
- Whatever suppresses it is on Mend's side, not GitHub's: GitHub delivers an app the events its
  own actions cause, so the event was sent and not acted on.
- Schedule-only cadence measured over 73 intervals: **median 6.7 h** (Mend documents 4 h for the
  Community tier). That is the window a stale PR stays unmergeable.

The *mechanism* is inference, not measurement: sender-based filtering is the natural explanation
(Renovate pushes to its own branches constantly, so a handler that didn't ignore itself would loop),
but Mend's scheduling layer is not in the OSS image and nothing here proves which rule fired. Treat
the observation as established and the cause as likely.

The lever is the Mend-only `<!-- manual job -->` checkbox on the Dependency Dashboard — Mend's,
not OSS Renovate's (`manual job` appears nowhere in the `renovate/renovate` dist), so it can move
or vanish without a Renovate release. Ticking it as a different sender emits an `issues.edited`
webhook Mend *does* enqueue. The decision logic is a pure function in
`meta/scripts/renovate_manual_job.py` (tested at `//meta/scripts:test_renovate_manual_job`); the
workflow holds only the API calls.

Two things worth knowing before changing this:

- **`merged_by.login` is `renovate[bot]`, not `app/renovate`.** GraphQL (`gh pr view --json
  mergedBy`) reports the latter; the REST payload the workflow gates on reports the former.
  Matching the GraphQL spelling would make the workflow silently never fire.
- **The sender assumption is the load-bearing risk.** The workflow ticks the box as
  `github-actions[bot]`, on the theory that Mend filters `renovate[bot]` specifically rather than
  every bot. If a run stops appearing after an automerge, re-test with `workflow_dispatch` and
  check the job log; the fallback is to make the edit as the `Renovate helper` app instead, which
  would need `Issues: read & write` added to its permissions.

## .devcontainer plumbing and feature pins

The container-side host plumbing — worktree git resolution, the host timezone, the shared git index
across two stat domains, and signed commits under the `devcontainer` CLI — is **not documented
here**. It ships as a shared base image, and its canonical home is
[`meta/devcontainer-base/README.md`](../meta/devcontainer-base/README.md), so `Syndic/.dotfiles`
reads one copy instead of a fork of this file that drifts.

What is local to this repo:

- **`.devcontainer/Dockerfile` `FROM`s that image at a pinned digest** and layers go/bazel/uv on
  top. The `BASE_IMAGE` override and the exact three-line shape it needs are under "Consuming the
  image" in that README; `.devcontainer/test_devcontainer_config.py` asserts the couplings, because
  every wrong shape fails at container-build time or not at all.
- **That pin is a derived file, not a dependency.** The digest is reproducible from source, so the
  PR that changes the image carries the new pin. Three callers, one per source of change: the
  `base-image-pin` pre-commit hook for our edits, this workflow for Renovate's, and
  `//.devcontainer:test_base_image_pin` as the check under both — hooks are bypassable and the
  workflow only fires for Renovate's own PRs. Renovate is configured to ignore the dep. The cost
  is that a base-editing branch pins an image the registry doesn't have yet; set
  `DEVCONTAINER_BASE_IMAGE` to the published `:latest` to keep working. Don't reach for
  `bazel run :load` locally — it needs a Docker daemon the devcontainer doesn't have, which is why
  that path is CI's.
- **`.devcontainer/initialize.sh` is the host stub** — the read-and-drop half the image cannot
  carry, since it runs on the host before any container exists. It writes `.git-plumbing/` and the
  `.host-*` symlinks `devcontainer.json` binds.
- **The hooks call `/usr/local/bin/devcontainer-plumbing` first, then do repo work.**
  `post-start.sh` additionally installs the host gitconfig, repoints `gpg.ssh.allowedSignersFile`
  and chowns the agent socket; those steps stay here until a second consumer needs them.
- **`.git-plumbing/` is tracked via its README**; the files inside are gitignored and rewritten on
  every `up`.

`.devcontainer/devcontainer-lock.json` pins a resolved version + digest for each
`ghcr.io/devcontainers/features/*` feature referenced from `devcontainer.json`. It is a **derived
file** in exactly the sense `MODULE.bazel.lock` is: `renovate-derived-files.yml` regenerates it with
`devcontainer upgrade` whenever a PR moves `devcontainer.json`, and commits it in that workflow's
single commit, and `devcontainer.yml` fails the build if the committed copy doesn't match what
`upgrade --dry-run` would write. That check is in CI rather than pre-commit — unlike `bazel mod tidy`
or `uv lock`, the devcontainer CLI is a host/runner tool and is deliberately absent from the image,
so a `language: system` hook running *inside* the container could not invoke it.

To re-resolve the lock by hand: `devcontainer upgrade --workspace-folder .` (`--dry-run` prints
instead of writing). Note that under exact pins `upgrade` can no longer *advance* anything — it only
makes the lock agree with the references. Moving a feature version means editing the tag in
`devcontainer.json`; `devcontainer outdated` will report Wanted/Latest equal to the pin, not the
newest release.

Two conventions make that work, and both are load-bearing:

- **Feature references are pinned to full semver** (`features/go:1.3.4`), not the floating major
  tags (`:1`) the devcontainer templates emit. Renovate's `devcontainer` manager tracks features as
  `docker` deps, so an exact tag is a version it can bump — and that bump is the event the derived
  file regenerates from. Under a floating `:1` there is nothing for Renovate to propose, no PR, and
  therefore no trigger; the lock then drifts with nothing to notice. It did: it sat two features
  behind (common-utils 2.5.8→2.5.9, git 1.3.5→1.3.8) for as long as nobody thought to look.
- **Digests are not pinned in the reference.** The devcontainer spec supports `@sha256:` on `image`
  but not on features. Renovate's `pinDigests` output — `features/go:1.3.4@sha256:…` — makes the
  CLI **silently drop the feature**: no error, it simply stops being installed. (The tagless
  `features/go@sha256:…` form does resolve, but carries no version for Renovate to compare, so it
  would be pinned forever and invisible.) `renovate.json` therefore sets `pinDigests: false` for the
  `devcontainer` manager. Digest pinning is not lost — the lock records
  `resolved: …@sha256:…` per feature, which is where the digest belongs.

Note the lock is keyed by the *reference string*, so a version bump restales every entry rather than
just the one field — which is why the regeneration is a full `devcontainer upgrade` and not a patch.

Python is deliberately **not** a devcontainer feature: that feature compiles CPython from source
(~2 min per build). The Dockerfile installs a prebuilt uv-managed interpreter instead — `ARG
PYTHON_VERSION` (Renovate-tracked via the Dockerfile custom manager, `depName=python`, so it stays
in the "Language toolchain SDKs" group alongside `//:.python-version`, `MODULE.bazel`, and
`pyproject.toml`'s `requires-python`) — and
symlinks `python3`/`python` onto PATH. The CI `Devcontainer` job caches the built image in GHCR
(`imageName`/`cacheFrom`, `push: filter` seeds it on pushes to main), so the feature layers are
reused across runs rather than rebuilt cold; this needs the workflow's `packages: write`.

## Devcontainer cache volumes

Two named volumes carry derived state across a container rebuild, and both are mounted at a
cache **root** rather than at a per-tool directory:

- `ud-cache` → `/home/vscode/.cache`
- `ud-go-pkg-cache` → `/go/pkg` (`$GOPATH/pkg`; `features/go` bakes `GOPATH=/go` into the image
  ENV, so this is not under `$HOME` at all)

**A mount too narrow loses caches silently.** It only persists the tools someone remembered to
enumerate, and nothing fails when one is missed — the container just rebuilds that cache every
time, which reads as "devcontainers are slow" rather than as a bug. That had already happened:
the mount was `~/.cache/bazel`, so its four siblings — `go-build` (1.9G), `bazelisk` (61M),
`pre-commit` (13M), `uv` — were rebuilt on every recreate. Narrow mounts also break outright
when the omitted sibling is not optional: mounting `/go/pkg/mod` rather than `/go/pkg` leaves
the checksum-db cache (`/go/pkg/sumdb`) out, and Docker creates the `/go/pkg` mountpoint parent
root-owned, so the first `go install` fails on `open /go/pkg/sumdb/…: no such file or
directory`.

**A mount too wide shadows image content, also silently.** Docker seeds a named volume from the
image only while the volume is empty; after that the volume wins. So a volume over all of `/go`
would freeze `/go/bin` at whatever the image held on first mount, and a later `features/go` bump
would install tools nobody ever sees. `/go/bin` is image content — the feature builds ten tools
there at image-build time — while `/go/pkg` does not exist in the image at all, because the
feature purges the module cache afterwards. So `pkg/` is the derived half of GOPATH and `bin/`
is the artifact half, and only `pkg/` is mounted. `post-create.sh` reinstalls its six pinned
tools over the image's copies on every create, which is why persisting `/go/bin` would buy
nothing even without the shadowing.

Nothing under either mount should stay ephemeral: every entry is content-addressed or
key-validated by its own tool, and CI builds cold, so a stale local cache can't reach `main`.

Two couplings, both asserted by `.devcontainer/test_devcontainer_config.py`:

- **post-create.sh must chown every volume target.** Docker attaches a volume root-owned unless
  the image has a directory at the target to seed ownership from, and it has one at neither of
  these — so a mount added without a chown entry is unwritable by `remoteUser`. The
  chown is guarded on current ownership: warm, `~/.cache` is tens of GB, and recursing it on
  every rebuild is minutes of re-asserting what is already right.
- **`$GOPATH/pkg` is not derivable from anything in this repo** — `GOPATH` is the go feature's
  own value, baked into the image ENV — so the test pins it as a constant.

The volumes are per-host, not per-worktree, and shared by every checkout of this repo. That is
fine for both: Bazel namespaces its output base by workspace path, and the module cache is
content-addressed. It does mean a mount-target change is visible from other worktrees' running
containers, which still mount the old volume at the old path — a volume's content is shared, its
mount point is per-container.

## Agent skills

### Issue tracker

Issues live in GitHub Issues on `Syndic/unnatural_designs`, driven via the `gh` CLI. See
`docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage roles, each label string equal to its name. See
`docs/agents/triage-labels.md`.

### Domain docs

Multi-context: a root `CONTEXT-MAP.md` points at a per-context `CONTEXT.md`, one per Bazel package
tree. See `docs/agents/domain.md`.
