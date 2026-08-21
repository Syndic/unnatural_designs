# Configuration reference

The policy config is a JSON file that controls which checks run and adjusts the behavior of
individual checks. Pass it with `-config <path>` or `NETBOX_AUDIT_CONFIG=<path>`.

All fields are optional; omitting one keeps the documented default. Unknown keys are ignored
silently, so a misspelled section reads as "not configured" rather than as an error — a run that
stays green is not evidence that a new section was picked up. Check it against the names below.

---

## Top-level structure

Two keys: `checks` selects which checks run, `rules` tunes individual ones.

```json
{
  "checks": {},
  "rules": {
    "interface-vrf": {},
    "private-ip-vrf": {},
    "wireless-normalization": {},
    "rack-placement": {},
    "poe-power": {}
  }
}
```

Every key under `rules` is a **check ID** — the same string `checks.enabled` / `checks.disabled`
take, and the same one [CHECKS.md](CHECKS.md) documents each check under. Only the five checks above
read rules; the other thirteen have no knobs.

---

## `checks`

Controls which checks are included in a run.

```json
"checks": {
  "enabled": ["cables", "macs", "dhcp-reservations"],
  "disabled": ["device-type-drift"]
}
```

| Field      | Type       | Default        | Description                                                        |
| ---------- | ---------- | -------------- | ------------------------------------------------------------------ |
| `enabled`  | `string[]` | _(all checks)_ | If provided, **only** these check IDs run. All others are skipped. |
| `disabled` | `string[]` | `[]`           | Check IDs to skip. Applied after `enabled`.                        |

`enabled` and `disabled` can be used together: `enabled` acts as an allowlist, then `disabled`
removes specific entries from that list. An unrecognized ID in either list is a fatal error, so a
typo here fails the run rather than silently skipping a check.

Valid check IDs: `required-device-fields`, `device-locations`, `parent-placement`, `rack-placement`,
`device-type-drift`, `honeypots`, `wireless-normalization`, `poe-power`, `interface-vrf`,
`private-ip-vrf`, `ip-vlan`, `cables`, `patch-panel`, `modules`, `macs`, `dhcp-reservations`,
`planned-devices`, `switch-link-symmetry`.

---

## `rules.interface-vrf`

Controls the `interface-vrf` check, including which device roles are treated as WAN-side. Devices
in those roles — and interfaces cabled to them — are exempt from the VRF requirement, since
ISP-managed equipment sits outside the routing contexts NetBox models.

```json
"rules": {
  "interface-vrf": {
    "wan_device_roles": ["ISP Equipment"],
    "require_on_interfaces": true
  }
}
```

| Field                   | Type       | Default             | Description                                                                    |
| ----------------------- | ---------- | ------------------- | ------------------------------------------------------------------------------ |
| `wan_device_roles`      | `string[]` | `["ISP Equipment"]` | Device role names to treat as WAN-side.                                        |
| `require_on_interfaces` | `bool`     | `true`              | Flag in-use interfaces that have no VRF assigned (excluding WAN-side devices). |

---

## `rules.private-ip-vrf`

Controls the `private-ip-vrf` check.

```json
"rules": {
  "private-ip-vrf": {
    "require_on_private_ips": true,
    "require_on_public_ips": false
  }
}
```

| Field                    | Type   | Default | Description                                              |
| ------------------------ | ------ | ------- | ---------------------------------------------------------- |
| `require_on_private_ips` | `bool` | `true`  | Flag private IP addresses that have no VRF assigned.     |
| `require_on_public_ips`  | `bool` | `false` | Also flag public IP addresses that have no VRF assigned. |

---

## `rules.wireless-normalization`

Controls the `wireless-normalization` check.

```json
"rules": {
  "wireless-normalization": {
    "suppress_if_connected_wired_interface_is_complete": true,
    "require_mode": true,
    "require_untagged_vlan": true,
    "require_primary_mac": true
  }
}
```

| Field                                               | Type   | Default | Description                                                                                                                                               |
| --------------------------------------------------- | ------ | ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `suppress_if_connected_wired_interface_is_complete` | `bool` | `true`  | Skip wireless checks for a device that already has a fully-configured wired interface. Useful for dual-homed devices where the wired port is the primary. |
| `require_mode`                                      | `bool` | `true`  | Require an 802.1Q mode to be set on wireless interfaces.                                                                                                  |
| `require_untagged_vlan`                             | `bool` | `true`  | Require an untagged VLAN to be assigned on wireless interfaces.                                                                                           |
| `require_primary_mac`                               | `bool` | `true`  | Require a primary MAC address to be designated on wireless interfaces.                                                                                    |

---

## `rules.rack-placement`

Controls the `rack-placement` check.

```json
"rules": {
  "rack-placement": {
    "exempt_child_devices": true,
    "exempt_device_tags": ["0u-rack-device"]
  }
}
```

| Field                  | Type       | Default              | Description                                                                                                                                                   |
| ---------------------- | ---------- | -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `exempt_child_devices` | `bool`     | `true`               | Skip the check for devices that are installed inside a parent device. Child devices do not occupy their own rack unit.                                        |
| `exempt_device_tags`   | `string[]` | `["0u-rack-device"]` | Devices carrying any of these tags are exempt from U position and face requirements. Use this for zero-U items such as PDUs, patch panels, or cable managers. |

`exempt_device_tags` **replaces** the default rather than extending it, so keep `0u-rack-device` in
the list when adding a tag. Entries are matched against tag slugs, trimmed of surrounding
whitespace.

---

## `rules.poe-power`

Controls the `poe-power` check.

```json
"rules": {
  "poe-power": {
    "check_powered_device_supply": true,
    "require_pse_mode_on_peer": true,
    "unknown_type_policy": "fail"
  }
}
```

| Field                         | Type     | Default  | Description                                                                                                |
| ----------------------------- | -------- | -------- | ---------------------------------------------------------------------------------------------------------- |
| `check_powered_device_supply` | `bool`   | `true`   | Enable or disable the PoE check entirely.                                                                  |
| `require_pse_mode_on_peer`    | `bool`   | `true`   | Flag a finding when a PD interface is connected to a peer that is not marked as PSE mode.                  |
| `unknown_type_policy`         | `string` | `"fail"` | How to handle a PD or PSE with no PoE type set. `"fail"` flags it as a finding; `"ignore"` skips silently. |

`unknown_type_policy` is the one value validated on load: anything other than `"fail"`, `"ignore"`,
or the empty string aborts the run rather than falling back to a default. Case and surrounding
whitespace are normalized.

### PoE type hierarchy

The check uses this ordering to determine whether a supply is sufficient for a demand:

| Type                | Standard | Max wattage |
| ------------------- | -------- | ----------- |
| `type1-ieee802.3af` | 802.3af  | 15.4 W      |
| `type2-ieee802.3at` | 802.3at  | 30 W        |
| `type3-ieee802.3bt` | 802.3bt  | 60 W        |
| `type4-ieee802.3bt` | 802.3bt  | 100 W       |

A supply of type N satisfies any demand of type ≤ N.

---

## Example config

```json
{
  "checks": {
    "disabled": ["device-type-drift"]
  },
  "rules": {
    "interface-vrf": {
      "wan_device_roles": ["ISP Equipment", "ISP Router"],
      "require_on_interfaces": true
    },
    "private-ip-vrf": {
      "require_on_private_ips": true,
      "require_on_public_ips": false
    },
    "wireless-normalization": {
      "suppress_if_connected_wired_interface_is_complete": true,
      "require_mode": true,
      "require_untagged_vlan": true,
      "require_primary_mac": true
    },
    "rack-placement": {
      "exempt_child_devices": true,
      "exempt_device_tags": ["0u-rack-device", "wall-mount"]
    },
    "poe-power": {
      "check_powered_device_supply": true,
      "require_pse_mode_on_peer": true,
      "unknown_type_policy": "ignore"
    }
  }
}
```

The repo's own policy file, [`netbox_audit.config.json`](../../netbox_audit.config.json), is the
same shape with every value at its default.
