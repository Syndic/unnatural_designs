# netbox_audit

A Go tool that audits a NetBox network inventory for consistency issues. It loads a coherent snapshot of all NetBox data, runs 18 configurable checks in parallel, and reports findings.

## Quick start

From the root of the repository:

```bash
go run ./cmd/netbox_audit \
  -netbox-base-url http://mini.dev.yanch.ar:8000 \
  -netbox-token-file ../.netbox_api_token
```

By default the tool prints a human-readable report to stdout and progress messages to stderr. See [OPERATION.md](OPERATION.md) for all flags, environment variables, and output formats.

## How it works

1. **Snapshot** — A snapshot of NetBox is taken: First, the latest changelog entry is read. Then all data is fetched in parallel. Finally, the latest changelog entry is compared to the first. If the two entries differ it implies NetBox has changed mid-fetch and the snapshot must be retried. This guarantees that checks operate on a single consistent point-in-time view.

2. **Checks** — The configured checks run in parallel against the in-memory snapshot. Failed checks produce "findings". Findings are sorted before output to make reports reproducible.

3. **Report** — Results are written to stdout as either formatted text (with optional ANSI color) or JSON. A non-zero exit code can be requested when findings exist, making the tool CI-friendly.

## Checks

See [CHECKS.md](CHECKS.md) for a detailed description of every check, including what it validates, why it matters, and which configuration knobs affect it.

The 18 checks are:

| ID | Name |
|----|------|
| `required-device-fields` | Required device fields |
| `device-locations` | Device locations |
| `parent-placement` | Parent/child placement agreement |
| `rack-placement` | Rack U-position and face |
| `device-type-drift` | Device type drift |
| `honeypots` | Honeypot coverage |
| `wireless-normalization` | Wireless interface normalization |
| `poe-power` | PoE power supply adequacy |
| `interface-vrf` | Interface VRF assignment |
| `private-ip-vrf` | Private IP VRF assignment |
| `ip-vlan` | IP/VLAN consistency |
| `cables` | Cable completeness |
| `patch-panel` | Patch panel cross-mappings |
| `modules` | Module bay consistency |
| `macs` | MAC address uniqueness |
| `dhcp-reservations` | DHCP reservation validity |
| `planned-devices` | Planned device cleanliness |
| `switch-link-symmetry` | Switch-to-switch link symmetry |

## Configuration

A JSON policy file controls which checks run and adjusts their behavior. See [CONFIG.md](CONFIG.md) for the full schema.

```bash
go run ./cmd/netbox_audit -config netbox_audit.config.json
```

## Build

The module uses only the Go standard library (no third-party dependencies).

```bash
cd network_maintainence_tools
go build ./cmd/netbox_audit
./netbox_audit -netbox-token-file ../.netbox_api_token
```
