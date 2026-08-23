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

`check_modules.py`'s matrix check parses the workflow as YAML (`_workflows.py`, the one module
here with a third-party dependency), so how a matrix is *written* — block or flow style,
`include:` items, quoting, comments, nesting — is not something the check has an opinion about.
`exclude:` entries are recognised and deliberately not collected: excluding a combination does
not remove the module from the axis it was drawn from.

What is left is what no static check can resolve: a computed matrix (`matrix: ${{ fromJSON(…) }}`)
or an axis that is not a list of paths. Those are *reported*, via `unrecognised_matrix_keys`,
rather than skipped — a matrix nobody can read is otherwise indistinguishable from a workflow
that has none, and the second reading is the one that passes.

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

`_workspace.py` is a private shared helper for the five guards above (Bazel workspace discovery,
module enumeration). The leading underscore signals it's not a public API; `test__workspace.py`
covers it directly.

`test_precommit_docs.py` has no script half. It asserts that README's pre-commit hook table, and
the paragraph that classifies each hook, still agree with `.pre-commit-config.yaml` — a coupling
between two checked-in files rather than a check over the tree, so the assertion is the whole gate
and it rides `bazel test //...` instead of costing a CI job.

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
