# Reminder Tags

Reminder tags are structured comments placed in source, config, and tooling files to communicate
intent to future readers and contributors. They are greppable by design — each tag is a distinct
string so you can find every instance of a given category with a single command.

---

## `TODO:`

Indicates a task that needs to be done but has not been started yet.

```sh
grep -rn "TODO:" .
```

---

## `FIXME:`

Highlights code that is broken, incomplete, or known to need improvement. Priority and issue
reference are optional but encouraged.

**Format:** `FIXME<priority><issue>: <description>`

| Field        | Values                                                                            |
| ------------ | --------------------------------------------------------------------------------- |
| `<priority>` | _(omitted)_ low priority or optimization · `!` significant · `!!` severe/frequent |
| `<issue>`    | _(omitted)_ or `(Issue #N)` linking to the relevant GitHub issue                  |

**Examples:**

```
FIXME: this is harder to read than it needs to be
FIXME!: only handles ASCII input, should support UTF-8
FIXME!! (Issue #42): panics on empty slice
FIXME! (Issue #7): works for the common case but misses edge cases added in #12
```

**When to use `FIXME` vs. `HACK`:** Use `FIXME` when a better approach is known or intended. Use
`HACK` when the current approach is undesirable but no clear improvement path exists yet.

```sh
grep -rn "FIXME" .
```

---

## `HACK:`

Marks a workaround or non-ideal solution that was chosen deliberately. The comment should explain
what makes the approach undesirable and why it was chosen anyway.

If a better approach is known but blocked on something else, prefer `FIXME` with a note about the
blocker. (Ideally, there would be an issue for this, and an issue for the blocker, and the issues
would be linked.)

```sh
grep -rn "HACK:" .
```

---

## `NOTE:`

Provides context, explains non-obvious logic, or documents a tradeoff. Use `NOTE` when the choices
made are generally acceptable but complex enough to benefit from commentary. If the approach is
actively undesirable, consider `HACK` or `FIXME` instead.

```sh
grep -rn "NOTE:" .
```

---

## `TEND(<task type>):`

Marks something that is correct and complete at the time of writing, but will need attention in the
future when something else in the repo changes. Unlike `FIXME`, there is nothing wrong with it now —
the flag is a forward-looking reminder, not a criticism of current state.

**Format:** `TEND(<task-type>): <what to do and when>`

```sh
grep -rn "TEND(" .
```

### Task types

#### `lang-expand`

The marked config or job covers only the currently-adopted languages. When a new language is brought
into the repo, grep for this task type and make the corresponding addition at each location.

```sh
grep -rn "TEND(lang-expand)" .
```

#### `optimization`

Marks a configuration or setting that may be suboptimal and should be reviewed in the future.

```sh
grep -rn "TEND(optimization)" .
```

---

## Adding a new tag or task type

- **New top-level tag:** Document it in this file following the same structure. Keep the tag itself
  short, uppercase, and distinct from existing tags.
- **New `TEND` task type:** Add it under the task types section above with a description and a grep
  command. The comment at the call site should say enough that a reader knows what to do without
  opening this file.
