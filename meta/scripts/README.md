# meta/scripts

Repo-health gates. Each `check_*.py` enforces a cross-cutting invariant that doesn't fit inside a
single language toolchain — they run in CI, in pre-commit (where they fix or block), and on save
in the editor (where they surface findings without blocking).

| Script                  | Enforces                                                                                       | CI job (`.github/workflows/`) | Pre-commit hook       | On-save (`.vscode/settings.json`) |
| ----------------------- | ---------------------------------------------------------------------------------------------- | ----------------------------- | --------------------- | --------------------------------- |
| `check_modules.py`      | Go module matrix/config and Python workspace/lock invariants are consistent                    | `ci.yml`, `security.yml`      | —                     | `check: modules`                  |
| `check_go_work.py`      | Every Go module in the repo is registered in `go.work`                                         | `ci.yml`                      | —                     | `check: go work`                  |
| `check_no_cgo.py`       | No `import "C"` in our Go source and no transitive deps that compile C/C++/cgo/SWIG            | `ci.yml`                      | —                     | —                                 |
| `check_secrets_dir.py`  | `secrets/` contains no committed files other than `secrets.md`                                 | `ci.yml`                      | `check-secrets-dir`   | —                                 |
| `check_release_please.py` | release-please config/manifest valid, consistent, and complete (every Go module is a package, Go tag shape correct) | — _(pending)_        | — _(pending)_         | —                                 |

`_workspace.py` is a private shared helper for the four guards above (Bazel workspace discovery,
module enumeration). The leading underscore signals it's not a public API; `test__workspace.py`
covers it directly.

`smoke_py/` is a transient `py_test` that proves the end-to-end Python plumbing chain
(`pyproject.toml` → `uv.lock` → `requirements_lock.txt` → `pip.parse` → `@pypi//...`) by importing
`requests` and asserting it loads. Slated for deletion once gazelle_python is wired (see
`docs/future-considerations.md` "Python BUILD Generation").

Failure format across the guards is `path:line: message` so VS Code task matchers can turn each
finding into a Problems-panel entry with a squiggle at the offending line.
