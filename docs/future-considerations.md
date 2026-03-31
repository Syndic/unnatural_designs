# Future Considerations

Items flagged during development as worth revisiting when the time is right.

---

## Bazel Remote Execution

Remote caching (Buildbuddy) is in place. The next step is remote execution —
farming build actions out to a pool of workers. This would allow parallelising
builds across local machines.

Relevant tooling: `buildfarm` (open source), or Buildbuddy's paid RBE tier.

---

## Local Secret Management

Currently, local Bazel remote cache credentials are stored in `.bazelrc.user`
(gitignored). This is acceptable for a single-user machine but not rigorous.

A proper solution would retrieve secrets from a secrets manager (e.g. macOS
Keychain, 1Password CLI) rather than storing them in a file on disk.

---

## Gazelle Python Support

Gazelle has a Python plugin bundled with `rules_python` that can auto-generate
`py_library`, `py_binary`, and `py_test` targets — equivalent to what it does
for Go. It was not configured because the Python footprint is currently small.

This is actively tracked: the `python-scale-check` CI job and pre-commit hook
will fail when the py_* target count exceeds the configured threshold, at which
point this item should be actioned.
