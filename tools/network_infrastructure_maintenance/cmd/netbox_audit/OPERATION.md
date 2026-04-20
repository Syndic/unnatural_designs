# Operation reference

## CLI flags

| Flag                     | Default                         | Description                                             |
| ------------------------ | ------------------------------- | ------------------------------------------------------- |
| `-netbox-base-url`       | `http://mini.dev.yanch.ar:8000` | NetBox server URL                                       |
| `-netbox-token-file`     | `.netbox_api_token`             | Path to a file containing the API token                 |
| `-config`                | _(none)_                        | Path to a JSON policy config file                       |
| `-format`                | `text`                          | Output format: `text` or `json`                         |
| `-color`                 | `auto`                          | ANSI color in text output: `auto`, `always`, or `never` |
| `-progress`              | `auto`                          | Progress UI mode: `auto`, `rich`, `plain`, or `off`     |
| `-max-snapshot-attempts` | `5`                             | How many times to retry an incoherent snapshot          |
| `-snapshot-retry-delay`  | `3s`                            | Delay between snapshot retries                          |
| `-fail-on-findings`      | `false`                         | Exit with code 2 if any check produces findings         |

## Environment variables

All flags can be set via environment variables. CLI flags take precedence over environment
variables.

| Variable              | Equivalent flag                                                                                               |
| --------------------- | ------------------------------------------------------------------------------------------------------------- |
| `NETBOX_BASE_URL`     | `-netbox-base-url`                                                                                            |
| `NETBOX_TOKEN`        | _(inline token, alternative to token file)_                                                                   |
| `NETBOX_TOKEN_FILE`   | `-netbox-token-file`                                                                                          |
| `NETBOX_AUDIT_CONFIG` | `-config`                                                                                                     |
| `NETBOX_AUDIT_COLOR`  | `-color`                                                                                                      |
| `NO_COLOR`            | Sets color to `never` (standard convention); also forces `-progress plain` when `-progress auto` is in effect |

`NETBOX_TOKEN` lets you supply the token directly without a file, which is useful in CI environments
where writing a token file is awkward.

## Progress UI

The `-progress` flag controls how snapshot-fetch and check-execution progress is rendered to
**stderr** (the final report on stdout is unaffected).

| Mode             | Behaviour                                                                                                                                                                                                                                                                                                                                                                                         |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `auto` (default) | `rich` when stderr is an interactive TTY, `NO_COLOR` is unset, and `TERM` is not `dumb` or empty. Otherwise `plain`.                                                                                                                                                                                                                                                                              |
| `rich`           | A pinned multi-line region at the bottom of the terminal: aggregate progress bar plus one bar per in-flight collection (rendered as a spinner until NetBox returns the collection's first page, then a determinate bar). Completed collections scroll above as one-line summaries. Checks are summarised with one start line, one line per check that produced findings, and one completion line. |
| `plain`          | Line-oriented logs suitable for CI pipelines and pipes. One line per snapshot-collection completion, one line per snapshot retry, and a single check-phase summary line.                                                                                                                                                                                                                          |
| `off`            | No progress output on stderr at all. The final report on stdout is unaffected.                                                                                                                                                                                                                                                                                                                    |

Force `-progress rich` when running through a wrapper that misreports TTY status (e.g. some `tmux`
configurations or recording tools); force `-progress plain` for log capture even when running in a
TTY.

## Output: text format

The default output is designed to be read by a human. Structure:

```
Snapshot: attempt 1/5, latest change #12345 at 2026-03-25T10:00:00Z
Checks: 18 total, 3 with findings

PASS  required-device-fields (0 findings, 45ms)
WARN  rack-placement (2 findings, 12ms)
  - switch-core: missing U position
  - ap-hallway-01: missing face
...

Timing: total 1.2s, snapshot 0.8s
```

- `PASS` lines are green, `WARN` lines are orange (when color is enabled).
- Progress messages (snapshot retries, check completions) go to **stderr** and do not appear in the
  report.
- Findings within each check are sorted alphabetically for reproducible output.

## Output: JSON format

```bash
go run ./cmd/netbox_audit -format json
```

Produces a single JSON document:

```json
{
  "snapshot": {
    "attempts": 1,
    "latest_change_id": 12345,
    "latest_change_time": "2026-03-25T10:00:00Z",
    "duration_ms": 800
  },
  "checks": [
    {
      "id": "rack-placement",
      "name": "Rack U-position and face",
      "status": "warn",
      "findings": ["switch-core: missing U position"],
      "duration_ms": 12
    }
  ]
}
```

JSON output is suitable for piping into `jq`, storing as an artifact, or feeding downstream tooling.

## Exit codes

| Code | Meaning                                                                      |
| ---- | ---------------------------------------------------------------------------- |
| `0`  | Success — checks ran (findings may exist unless `-fail-on-findings` was set) |
| `1`  | Fatal error — could not load snapshot, invalid config, etc.                  |
| `2`  | Findings found and `-fail-on-findings` was set                               |

## Examples

```bash
# Basic run with a token file
go run ./cmd/netbox_audit -netbox-token-file ../.netbox_api_token

# JSON output, suppress color, exit non-zero on findings (CI usage)
go run ./cmd/netbox_audit \
  -format json \
  -color never \
  -fail-on-findings

# Use a custom policy that disables some checks
go run ./cmd/netbox_audit -config /etc/netbox_audit/policy.json

# Inline token via environment variable
NETBOX_TOKEN=abc123 go run ./cmd/netbox_audit
```
