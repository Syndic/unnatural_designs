# Future Considerations

Items flagged during development as worth revisiting when the time is right.

---

## Bazel Remote Execution

Remote caching (Buildbuddy) is in place. The next step is remote execution — farming build actions
out to a pool of workers. This would allow parallelising builds across local machines.

Relevant tooling: `buildfarm` (open source), or Buildbuddy's paid RBE tier.

---

## Local Secret Management

Currently, local Bazel remote cache credentials are stored in `.bazelrc.user` (gitignored). This
is acceptable for a single-user machine but not rigorous. Other secrets are stored in
//secrets, which is gitignored. I'd like to make sure that nothing ever gets committed there. I'd 
like to implement a check that nothing other than the markdown file is ever committed there.

A proper solution would retrieve secrets from a secrets manager (e.g. macOS Keychain, 1Password
CLI) rather than storing them in a file on disk.

---

## Renovate Trigger Frequency

Renovate appears to trigger on every branch push rather than only on changes to main. This is
more frequent than expected and worth investigating — it may be a configuration issue or a default
behaviour that can be tightened.

---

## Code Quality and Coverage Reporting

No code quality metrics or test coverage reporting is currently in place. Worth adding to CI once
there is meaningful code to measure. Candidate tooling:

- **Go**: `go test -coverprofile` + a coverage reporting service (e.g. Codecov, Coveralls)
- **Python**: `coverage.py` + the same reporting service

Code quality linting (e.g. `golangci-lint` for Go, `ruff` for Python) is also worth adding as a
CI job.

---

## Gazelle Python Support

Gazelle has a Python plugin bundled with `rules_python` that can auto-generate `py_library`,
`py_binary`, and `py_test` targets — equivalent to what it does for Go. It was not configured
because the Python footprint is currently small.

This is actively tracked: the `python-scale-check` CI job and pre-commit hook will fail when the
py_* target count exceeds the configured threshold, at which point this item should be actioned.
