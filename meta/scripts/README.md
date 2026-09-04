# meta/scripts

Repo-health gates. Each `check_*.py` enforces a cross-cutting invariant that doesn't fit inside a
single language toolchain. All of them run in CI; the table says which also run in pre-commit
(where a check can fix or block) and which run on save in the editor (where they surface findings
without blocking).

| Script                  | Enforces                                                                                       | CI job (`.github/workflows/`) | Pre-commit hook       | On-save (`.vscode/settings.json`) |
| ----------------------- | ---------------------------------------------------------------------------------------------- | ----------------------------- | --------------------- | --------------------------------- |
| `check_modules.py`      | Go module matrix/config and Python workspace/lock invariants are consistent (see below)        | `ci.yml`                      | —                     | `check: modules`                  |
| `check_go_work.py`      | Every Go module in the repo is registered in `go.work`                                         | `ci.yml`                      | —                     | `check: go work`                  |
| `check_no_cgo.py`       | No `import "C"` in our Go source and no transitive deps that compile C/C++/cgo/SWIG            | `ci.yml`                      | —                     | —                                 |
| `check_adr_numbers.py`  | ADR numbers are unique repo-wide and filenames are `NNNN-kebab-slug.md`         | `ci.yml`                      | —                     | `check: adr numbers`              |
| `check_secrets_dir.py`  | `secrets/` contains no committed files other than `secrets.md`                                 | `ci.yml`                      | `check-secrets-dir`   | —                                 |
| `check_python_version.py` | Every Python language-level declaration agrees with `//:.python-version` (see below)          | `ci.yml`                      | —                     | `check: python version`           |

`check_modules.py`'s matrix check parses the workflow as YAML (`_workflows.py`, the one module
here with a third-party dependency), so how a matrix is *written* — block or flow style,
`include:` items, quoting, comments, nesting — is not something the check has an opinion about.
Coverage is the base axis plus `include:`. `exclude:` is ignored in both directions — a module
excluded for cause has been handled appropriately, which is the question this check asks, and
`exclude:` can only ever shrink GitHub's cross product, so it can never be the thing that puts a
module into the run set.

What is left is an axis that is present under the key and cannot be read — a `fromJSON`
expression, say. That is *reported*, via `unrecognised_matrix_keys`, rather than skipped: a
matrix nobody can read is otherwise indistinguishable from a workflow that has none, and the
second reading is the one that passes.

A wholly computed `matrix:` is deliberately not reported. The key's presence is itself unknown
there, it is usually an unrelated axis, and failing CI on workflows that never mention the
language would reintroduce the false-positive class this replaced. The cost — a per-module
matrix converted to `fromJSON` stops being verified quietly — is tracked in #271.

This replaced a line-oriented scanner. It is worth knowing why, because the intermediate step is
the tempting one: the scanner missed flow style entirely, and widening its patterns to cover more
spellings produced a scan with no notion of matrix context, which flagged `workflow_call` inputs,
step `with:` values, job `env:` entries and lines inside `run: |` blocks. Structure is what tells
those apart, so the parser supplies structure. See #268 for the same change owed elsewhere.

`check_adr_numbers.py` also answers `--next`, which prints the next free ADR number and nothing
else. That is the supported way to pick one — the alternative is a repo-wide search, since the
numbering is global while the directories are per-context. It deliberately has no counter file to
read: the numbers already live in the filenames, and a second copy would be a derived file needing
its own freshness check, and a merge conflict on every concurrent ADR.

`check_python_version.py` holds the four copies of the language level that cannot read
`//:.python-version` — `pyproject.toml`'s `requires-python`, `MODULE.bazel`'s two
`python_version` call sites, and the devcontainer `ARG` — to the value in that file, and refuses
a `setup-python` step that carries a literal instead of reading it. Each of those copies fails
*silently* on drift, because a stale language level is still a valid one, so every tool stays
green while targeting the wrong version. ruff and ty are deliberately absent from the list: both
derive their target from `requires-python`, which is why neither `[tool.ruff] target-version` nor
`[tool.ty.environment] python-version` is set.

It reads steps through `_workflows.py` for the same reason `check_modules.py` reads matrices that
way, and it covers `.github/actions/*/action.yml` alongside `.github/workflows/*.yml` — a step
pins the level wherever it lives.

Two step shapes are rejected outright rather than interpreted. **Both inputs on one step**, because
`setup-python` prefers `python-version` and merely warns about the file, so `.python-version` reads
as the source while doing nothing — and an expression that resolves empty flips the action back to
the file, making the same YAML install different interpreters. **An expression it cannot resolve**,
because an unreadable pin is otherwise indistinguishable from a correct one, and the second reading
is the one that passes.

The single expression form accepted is a whole-value `${{ matrix.<axis> }}` reference whose axis
the job defines as a plain list — and that list must contain the pin. A job may test *more*
versions than the pin (testing a member across its supported range, #272) but never fewer.
Otherwise the level just moves into a list nothing verifies, and the job silently stops exercising
the version the rest of the repo targets as soon as the pin advances.

`_workspace.py` is a private shared helper for the six guards above (Bazel workspace discovery,
module enumeration). The leading underscore signals it's not a public API; `test__workspace.py`
covers it directly. `_workflows.py` is the same idea for GitHub Actions YAML, and is the only
module here with a third-party dependency — `check_modules.py` and `check_python_version.py` are
its two consumers, which is why both run under `uv run --frozen` in CI while the others use a
bare `python3`.

`path_classification_pattern_sets.py` is the single definition of every named pattern set this
repo classifies paths against. Unlike the two helpers above it has no leading underscore and no
logic at all — the sets are module-level constants composed by set union (`BASE = (..., *BAZEL)`),
so there is no format to parse and no resolver to get wrong. A set referenced by name inside the
module is an import-time `NameError` if misspelled, rather than a silently empty group; a set named
on `classify_changed_paths.py`'s command line is checked by its `select`, which refuses an unknown
name instead of emitting `name=false` forever. Being plain Python also keeps
`classify_changed_paths.py` dependency-free under a bare `python3`, which matters in the job that
feeds a required check. The constants carry their own rationale, so neither this file nor the
workflows restate which paths are in a set.

`classify_changed_paths.py` and `base_image_pin_hook.py` are its consumers. The first turns a
three-dot diff into `name=true|false` step outputs for `devcontainer.yml` and
`renovate-derived-files.yml`; the second is the `base-image-pin` pre-commit entry, which exists
because pre-commit's `files:` cannot read a shared definition — so the hook takes no filter, gates
on the shared set itself, and does nothing on a commit touching none of it.

`test_precommit_config.py` has no script half. It asserts that README's pre-commit hook table, and
the paragraph that classifies each hook, still agree with `.pre-commit-config.yaml`, and that every
hook under `repo: local` stays `language: unsupported` — pre-commit resolves any other language
itself, ignoring the interpreter this repo pins. `unsupported` rather than its deprecated alias
`system`, which pre-commit rewrites on load and will eventually drop. Neither is a check over the
tree, so the assertions are the whole gate and they ride `bazel test //...` instead of costing a
CI job.

`test_codeql_toolchain.py` has no script half either. It asserts that `security.yml`'s CodeQL job
installs the toolchain `go.work` names before extraction starts — the `actions/setup-go` step ahead
of `codeql-action/init`, plus the matrix entries that decide which languages need one at all. It
also holds the `codeql-all` fan-in to its two load-bearing properties, since that job is the name
branch protection requires. Same reason it rides `bazel test //...`: the couplings are between
checked-in files, and nothing fails while they drift — not until `go.work` outruns the runner
image's Go, or a green required check turns out to have been skipped.

`smoke_py/` is a transient `py_test` that proves the end-to-end Python plumbing chain
(`pyproject.toml` → `uv.lock` → `requirements_lock.txt` → `pip.parse` → `@unnatural_designs_pypi//...`) by importing
`requests` and asserting it loads. Slated for deletion once gazelle_python is wired (see
[#239](https://github.com/Syndic/unnatural_designs/issues/239)).

Failure format across the guards is `path:line: message` so VS Code task matchers can turn each
finding into a Problems-panel entry with a squiggle at the offending line.
