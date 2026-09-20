# The enforcement manifest is a demand about the effective rules, not a mirror of a ruleset

`ci_enforcement_manifest` records what must pass before a merge to `main`. It is a **demand**: it
blocks anything that isn't being validated the way we expect. Its subject is every rule
effectively applying to `main` — the union of whatever rulesets reach the branch — rather than the
contents of a specific ruleset.

## Considered options

### A mirror synced from the API — rejected

An automatically updated/synced mirror merely records an audit trail of how the rules changed over
time. While that could be useful to an extent, actually blocking until the rules match the manifest
leaves an audit trail AND ensures semantics enforced.

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

It's also just strictly less safe than we could be. No reason to be lazy about this.

### A demand compared for equality against the effective rules — accepted

`GET /repos/{owner}/{repo}/rules/branches/main` needs only `Metadata: read`, which an Actions token
carries unconditionally, and returns every rule reaching the branch with its parameters. The
manifest mirrors all of them and the comparison is equality, in both directions: a rule present
live and absent from the manifest fails, and so does the reverse.

Equality is deliberately strict rather than an allowlist of fields known to matter. Under an
allowlist a parameter GitHub adds is silently ignored, which is the fail-open direction; under
equality it is a loud red that one edit closes.

## Consequences

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
