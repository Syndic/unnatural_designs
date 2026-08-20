# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

## Repo-local labels

These are **not** triage roles. They sit orthogonal to the state role: an issue carries exactly one category role, exactly one state role, and any of these in addition.

| Label     | Meaning                                                                                                                       |
| --------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `blocked` | Waiting on a condition before the work can start. The condition is written in the issue body under a `**Trigger to revisit:**` heading. |

`blocked` covers both external gating (an upstream release, someone else's merge) and internal gating (wanting to see how a recent change behaves before choosing the next priority). The trigger line says which; the label deliberately does not, because one label with a precise sentence beats two labels with a fuzzy boundary between them.

A `blocked` issue keeps its state role, and `ready-for-agent` + `blocked` is the useful combination: the brief is written, so the moment the trigger fires an agent can take it. That is exactly why **`blocked` must be excluded from agent discovery** — see the agent-grabbable query in [issue-tracker.md](issue-tracker.md). An agent that grabs a blocked issue works on something it cannot finish.

`/triage` should surface `blocked` as a fourth bucket alongside unlabeled, `needs-triage`, and `needs-info`-with-reporter-activity. Sweeping those issues and checking whether any trigger has fired is what stops a dormant item from being silently forgotten — the failure mode that retired `docs/future-considerations.md`.
