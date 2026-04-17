# Configuration reference

The policy config is a JSON file that controls which checks run and adjusts the behavior of individual checks. Pass it with `-config <path>` or `NETBOX_AUDIT_CONFIG=<path>`.

All fields are optional. Omitting a field keeps the documented default.

---

## Top-level structure

```json
{
  "checks": { ... },
  "wan": { ... },
  "vrf": { ... },
  "wireless": { ... },
  "rack_placement": { ... },
  "poe": { ... }
}
```

---

## `checks`

Controls which checks are included in a run.

```json
"checks": {
  "enabled": ["cables", "macs", "dhcp-reservations"],
  "disabled": ["device-type-drift"]
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | `string[]` | _(all checks)_ | If provided, **only** these check IDs run. All others are skipped. |
| `disabled` | `string[]` | `[]` | Check IDs to skip. Applied after `enabled`. |

`enabled` and `disabled` can be used together: `enabled` acts as an allowlist, then `disabled` removes specific entries from that list.

Valid check IDs: `required-device-fields`, `device-locations`, `parent-placement`, `rack-placement`, `device-type-drift`, `honeypots`, `wireless-normalization`, `poe-power`, `interface-vrf`, `private-ip-vrf`, `ip-vlan`, `cables`, `patch-panel`, `modules`, `macs`, `dhcp-reservations`, `planned-devices`, `switch-link-symmetry`.

---

## `wan`

Identifies which device roles are considered WAN-side. Devices in these roles are excluded from VRF and interface checks that do not apply to ISP-managed equipment.

```json
"wan": {
  "device_roles": ["ISP Equipment"]
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `device_roles` | `string[]` | `["ISP Equipment"]` | Device role names to treat as WAN-side. |

---

## `vrf`

Controls VRF enforcement across the `interface-vrf` and `private-ip-vrf` checks.

```json
"vrf": {
  "require_on_private_ips": true,
  "require_on_public_ips": false,
  "require_on_interfaces": true
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `require_on_private_ips` | `bool` | `true` | Flag RFC1918 IP addresses that have no VRF assigned. |
| `require_on_public_ips` | `bool` | `false` | Also flag public IP addresses that have no VRF assigned. |
| `require_on_interfaces` | `bool` | `true` | Flag in-use interfaces that have no VRF assigned (excluding WAN-side devices). |

---

## `wireless`

Controls the `wireless-normalization` check.

```json
"wireless": {
  "suppress_if_connected_wired_interface_is_complete": true,
  "require_mode": true,
  "require_untagged_vlan": true,
  "require_primary_mac": true
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `suppress_if_connected_wired_interface_is_complete` | `bool` | `true` | Skip wireless checks for a device that already has a fully-configured wired interface. Useful for dual-homed devices where the wired port is the primary. |
| `require_mode` | `bool` | `true` | Require an 802.1Q mode to be set on wireless interfaces. |
| `require_untagged_vlan` | `bool` | `true` | Require an untagged VLAN to be assigned on wireless interfaces. |
| `require_primary_mac` | `bool` | `true` | Require a primary MAC address to be designated on wireless interfaces. |

---

## `rack_placement`

Controls the `rack-placement` check.

```json
"rack_placement": {
  "exempt_child_devices": true,
  "exempt_device_tags": ["0u-rack-device"]
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `exempt_child_devices` | `bool` | `true` | Skip the check for devices that are installed inside a parent device. Child devices do not occupy their own rack unit. |
| `exempt_device_tags` | `string[]` | `["0u-rack-device"]` | Devices carrying any of these tags are exempt from U position and face requirements. Use this for zero-U items such as PDUs, patch panels, or cable managers. |

---

## `poe`

Controls the `poe-power` check.

```json
"poe": {
  "check_powered_device_supply": true,
  "require_pse_mode_on_peer": true,
  "unknown_type_policy": "fail"
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `check_powered_device_supply` | `bool` | `true` | Enable or disable the PoE check entirely. |
| `require_pse_mode_on_peer` | `bool` | `true` | Flag a finding when a PD interface is connected to a peer that is not marked as PSE mode. |
| `unknown_type_policy` | `string` | `"fail"` | How to handle a PD or PSE with no PoE type set. `"fail"` flags it as a finding; `"ignore"` skips silently. |

### PoE type hierarchy

The check uses this ordering to determine whether a supply is sufficient for a demand:

| Type | Standard | Max wattage |
|------|----------|-------------|
| `type1-ieee802.3af` | 802.3af | 15.4 W |
| `type2-ieee802.3at` | 802.3at | 30 W |
| `type3-ieee802.3bt` | 802.3bt | 60 W |
| `type4-ieee802.3bt` | 802.3bt | 100 W |

A supply of type N satisfies any demand of type ≤ N.

---

## Example config

```json
{
  "checks": {
    "disabled": ["device-type-drift"]
  },
  "wan": {
    "device_roles": ["ISP Equipment", "ISP Router"]
  },
  "vrf": {
    "require_on_private_ips": true,
    "require_on_public_ips": false,
    "require_on_interfaces": true
  },
  "wireless": {
    "suppress_if_connected_wired_interface_is_complete": true,
    "require_mode": true,
    "require_untagged_vlan": true,
    "require_primary_mac": true
  },
  "rack_placement": {
    "exempt_child_devices": true,
    "exempt_device_tags": ["0u-rack-device", "wall-mount"]
  },
  "poe": {
    "check_powered_device_supply": true,
    "require_pse_mode_on_peer": true,
    "unknown_type_policy": "ignore"
  }
}
```
