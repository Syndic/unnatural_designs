# The enforcement manifest is a claim about the effective rules, not a mirror of a ruleset

`ci_enforcement_manifest` records what must pass before a merge to `main`. It is a **claim**: a
human writes it, CI compares it against what GitHub reports, and nothing automated may write any
part of it. Its subject is every rule effectively applying to `main` — the union of whatever
rulesets reach the branch — rather than the contents of a ruleset named by id.

## Considered options

### A mirror synced from the API — rejected

`.devcontainer/Dockerfile`'s `FROM` digest is a derived file with a sync script, a pre-commit hook
and a CI check, and reaching for that pattern here is the obvious move. It is wrong twice over.

`meta/CONTEXT.md` defines a **derived file** as one reproducible from other *checked-in sources*,
and distinguishes it from a **dependency**, whose value comes from outside the repo. The rules are
the second, so a synced manifest is not a derived file at all — it is a vendored copy, and a
freshness check over it verifies only that the copy is current, never that the value is right.

The failure that follows is the one this repo refuses everywhere else. Someone drops a required
check in the UI; the next sync rewrites the manifest to agree, commits it, and the gate is gone
behind a green tick and an auto-commit nobody reads. Under a claim the same edit turns CI red, and
accepting it costs a deliberate edit that a reviewer sees in the diff. A machine that can update
the claim to match reality leaves no claim behind, only a slower way of reading the API.

A human-invoked `--write` helper is a different thing and stays available if hand-editing ever
chafes. Frequent churn here is itself a signal something is wrong, so it is not built in advance.

### Reading a ruleset by id — rejected

`GET /repos/{owner}/{repo}/rulesets/{id}` needs `Administration: read`, and `administration` is not
a key the workflow `permissions:` block accepts, so no stanza can grant it — the route needs a PAT
or a new grant on the `Renovate helper` app.

The cost is not why it was rejected. It answers "what is in ruleset N", which equals "what gates a
merge to `main`" only while that is the only ruleset reaching the branch. Add a second one, at the
organisation or in the UI, and the read reports green while the gate has moved underneath it —
this ADR's own failure mode reappearing inside its own fix. No ruleset id appears in the
implementation, and the response is never filtered by one.

### Reconciling the required-status-check list alone — rejected

The narrow scope leaves two rules unverified that this repo's documents already reason from.
`strict_required_status_checks_policy` is why a branch that is `BEHIND` cannot merge, which is the
whole premise of `renovate-run-after-automerge.yml`; flip it off and that workflow becomes theatre.
The `code_scanning` rule is what gates on CodeQL's and Trivy's *findings* rather than on their
having run, and `.claude/CLAUDE.md` records that nothing could hold it. Both would have stayed
outside the manifest.

### A claim compared for equality against the effective rules — accepted

`GET /repos/{owner}/{repo}/rules/branches/main` needs only `Metadata: read`, which an Actions token
carries unconditionally, and returns every rule reaching the branch with its parameters. The
manifest mirrors all of them and the comparison is equality, in both directions: a rule present
live and absent from the manifest fails, and so does the reverse.

Equality is deliberately strict rather than an allowlist of fields known to matter. Under an
allowlist a parameter GitHub adds is silently ignored, which is the fail-open direction; under
equality it is a loud red that one edit closes.

## Consequences

- `REQUIRED` stops existing as hand-written data. `//meta/scripts:test_ci_enforcement_manifest`
  reads the mirror instead, keeping all five of its properties; `NOT_REQUIRED` stays hand-written,
  because what gates nothing *by decision* is a judgement the API cannot hold.
- GitHub ships new response fields inside an API version and treats them as non-breaking, so
  pinning `X-GitHub-Api-Version` does not help. Expect the check to go red on an untouched commit
  when a parameter is added, with no local cause. That is the accepted price of the paragraph
  above; the fix is a deliberate manifest edit, never a tolerance.
- Two blind spots are named rather than closed. `bypass_actors` is not returned by this endpoint
  without write access, so a rule list can match exactly while an actor may bypass it. The
  `Basic Tag Rules` ruleset is unreachable from a branch endpoint at all. Both need the credential
  rejected above, and tag rules gate no merge.
- A merge gate now sits behind an API call. This adds no new class of fragility — `Semgrep`,
  `Trivy`, `pip-audit` and `govulncheck` are all required and all fetch at run time — but it does
  mean a GitHub outage blocks merges for a reason unrelated to the tree. Transient failures retry
  a bounded number of times and then fail; a `4xx` and an unexpected shape fail at once, because
  those are the signal rather than noise.
- The check name is a string in repo settings, so renaming it costs a settings edit and a second
  pass through the ordering below. *Reconcile* is the wrong verb for what it does — it reports
  disagreement and refuses to act on it — and is avoided in the implementation.
- Enabling it is settings-first: the name joins the required list before the workflow lands, as
  with CodeQL's advanced setup. Until a branch carries the job, its check never reports and the PR
  waits, so open PRs rebase.
- The repo README, `meta/scripts/README.md` and the manifest's own header each state that a green
  test is not a verified ruleset. That stops being true here and the three copies are corrected
  together.
