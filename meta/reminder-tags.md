# Reminder Tags

Reminder tags are structured comments left at decision points in config and
tooling files. They mark places that need revisiting when something changes
about the repo — a new language adopted, a new tool category added, etc.

Each tag uses the format `# <tag>: <explanation>` so they are greppable and
self-documenting at the point where the work would actually be done.

---

## `lang-expand:`

**Purpose:** Marks a config or job that covers only the currently-adopted
languages. When a new language is brought into the repo, grep for this tag to
find every place that needs a corresponding addition.

**Where it appears:**

| File | What to do there |
|---|---|
| `.github/workflows/security.yml` — SAST section | Add a Semgrep rule pack (`p/<language>`) |
| `.github/workflows/security.yml` — Dependency CVE Scanning section | Add a dependency vulnerability scanner for the new language |
| `.github/workflows/security.yml` — Code Quality and Security Linting section | Add a linter job for the new language |
| `.github/workflows/security.yml` — Monorepo Structure Maintenance section | Add a completeness check (config file presence, module registration, etc.) |

**Find all occurrences:**

```sh
grep -rn "lang-expand:" .
```

---

## Adding a new tag

If you identify a new class of decision point that warrants a reminder tag,
use a short lowercase kebab-case name ending in `:` and document it here.
Keep the tag and its explanation co-located — the comment at the call site
should say enough that a reader knows what to do without consulting this file.
