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

## Code Coverage Reporting

`golangci-lint` is in place for Go code quality. No test coverage reporting is currently set up.
Worth adding to CI once there is meaningful code to measure. Candidate tooling:

- **Go**: `go test -coverprofile` + a coverage reporting service (e.g. Codecov, Coveralls)
- **Python**: `coverage.py` + the same reporting service

---

## Gazelle Python Support

Gazelle has a Python plugin bundled with `rules_python` that can auto-generate `py_library`,
`py_binary`, and `py_test` targets — equivalent to what it does for Go. It was not configured
because the Python footprint is currently small.

This is actively tracked: the `python-scale-check` CI job and pre-commit hook will fail when the
py\_\* target count exceeds the configured threshold, at which point this item should be actioned.
