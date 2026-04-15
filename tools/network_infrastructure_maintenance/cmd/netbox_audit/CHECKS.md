# Audit checks reference

All 18 checks run in parallel against an in-memory snapshot of NetBox data. Each check is independent and produces zero or more findings. Checks can be selectively disabled via the policy config (see [CONFIG.md](CONFIG.md)).

---

## `required-device-fields`

**What it checks:** Every device must have a site, a role, and a status.

**Why it matters:** Devices without these fields cannot be meaningfully placed in audit logic and indicate incomplete data entry.

---

## `device-locations`

**What it checks:** Every device must have a location assigned.

**Why it matters:** Location is used downstream for rack placement, site topology, and physical plant documentation. A device without a location is effectively untracked.

---

## `parent-placement`

**What it checks:** Child devices (devices installed inside another device) must agree with their parent on site, rack, and location.

**Why it matters:** NetBox allows parent and child fields to diverge if edited independently. Disagreements indicate stale data that will confuse location-based queries.

---

## `rack-placement`

**What it checks:** Devices mounted in a rack must have a U position and a face (front/rear) recorded.

**Why it matters:** Without position data, rack diagrams and capacity planning are unreliable.

**Configuration knobs:**
- `rack_placement.exempt_child_devices` — skip the check for devices that have a parent device (default: `true`; child devices don't occupy their own rack unit)
- `rack_placement.exempt_device_tags` — device tags that exempt a device from this check (default: `["0u-rack-device"]`; use this for zero-U items like patch panels or PDUs)

---

## `device-type-drift`

**What it checks:** The physical components present on each device instance (interfaces, console ports, power ports, power outlets, front ports, rear ports, device bays, module bays) must match what the device type definition says should be there.

Drift is reported as:
- **Missing** — a component exists in the type definition but not on the device
- **Extra** — a component exists on the device but not in the type definition
- **Type mismatch** — the component exists in both but with a different type

**Why it matters:** When a device type definition is updated or when a device is partially commissioned, instances can fall out of sync. Drift here causes other checks (cabling, PoE, modules) to produce false results.

**Note on modules:** Components that belong to an installed module use `{module}` placeholder names in the type definition (e.g., `{module}/eth0`). The check expands these against the actual module bay positions before comparing.

---

## `honeypots`

**What it checks:**
1. Every VLAN-backed subnet (prefix associated with a VLAN) must have at least one IP address tagged `honeypot`.
2. All IPs tagged `honeypot` must fall inside a VLAN-backed prefix.

**Why it matters:** Honeypots are canary IPs used for intrusion detection. The first condition ensures every VLAN has coverage. The second ensures there are no orphaned honeypot IPs pointing at subnets that no longer have a VLAN.

---

## `wireless-normalization`

**What it checks:** Wireless (Wi-Fi) interfaces on non-access-point devices must have:
- An 802.1Q mode set
- An untagged VLAN assigned
- A primary MAC address recorded

**Why it matters:** Wireless clients that connect to a managed switch port need proper VLAN tagging for correct network placement. Missing MAC addresses prevent DHCP reservations from being created.

**Configuration knobs:**
- `wireless.suppress_if_connected_wired_interface_is_complete` — skip the check for a device if it already has a fully-configured wired interface (default: `true`; avoids noise for dual-homed devices where the wired port is the primary)
- `wireless.require_mode` — require 802.1Q mode (default: `true`)
- `wireless.require_untagged_vlan` — require an untagged VLAN (default: `true`)
- `wireless.require_primary_mac` — require a primary MAC (default: `true`)

---

## `poe-power`

**What it checks:** Any interface in PoE PD mode (powered device) must be connected to an interface in PoE PSE mode (power-sourcing equipment) with a PoE type at least as capable as the PD's requirement.

PoE type hierarchy from least to most capable:
1. `type1-ieee802.3af` (15.4 W)
2. `type2-ieee802.3at` (30 W)
3. `type3-ieee802.3bt` (60 W)
4. `type4-ieee802.3bt` (100 W)

**Why it matters:** Mis-matched PoE types can cause devices to fail to power on or to operate in degraded mode.

**Configuration knobs:**
- `poe.check_powered_device_supply` — enable or disable the entire check (default: `true`)
- `poe.require_pse_mode_on_peer` — flag the finding if the connected peer is not marked as PSE (default: `true`)
- `poe.unknown_type_policy` — how to handle a PD or PSE with an unset PoE type: `"fail"` (flag as a finding) or `"ignore"` (skip silently) (default: `"fail"`)

---

## `interface-vrf`

**What it checks:** Every in-use interface (one that has IPs or cable connections) must have a VRF assigned, except interfaces on WAN-side device roles.

**Why it matters:** VRF assignment determines routing context. An interface without a VRF is ambiguously placed and can cause routing misconfigurations in tooling that reads from NetBox.

**Configuration knobs:**
- `vrf.require_on_interfaces` — enable or disable this check (default: `true`)
- `wan.device_roles` — device roles considered WAN-side, which are exempt from VRF requirements (default: `["ISP Equipment"]`)

---

## `private-ip-vrf`

**What it checks:** IP addresses in RFC 1918 private ranges (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16) must have a VRF assigned. Optionally, this check can also be applied to public IPs.

**Why it matters:** Private IP space is by definition non-globally-unique, so a private IP without a VRF is ambiguous. Tools that export routes or generate firewall rules from NetBox will produce incorrect results.

**Configuration knobs:**
- `vrf.require_on_private_ips` — enforce VRF on RFC1918 addresses (default: `true`)
- `vrf.require_on_public_ips` — also enforce VRF on public IP addresses (default: `false`)

---

## `ip-vlan`

**What it checks:** For interfaces in access mode (untagged/access port), the IP addresses assigned to that interface must belong to the subnet associated with the interface's untagged VLAN.

**Why it matters:** An IP outside the interface's VLAN subnet indicates either the VLAN assignment is wrong or the IP was assigned to the wrong interface. Either way, the device will not reach its default gateway.

---

## `cables`

**What it checks:** Every cable must have:
- A cable type recorded
- A status recorded
- Both an A-side and a B-side termination

**Why it matters:** Incomplete cables indicate data entry that was never finished. Cables missing terminations break connectivity graphs used by the cabling and port-mapping checks.

---

## `patch-panel`

**What it checks:** Front ports and rear ports that are connected via cable must have a cross-mapping to their corresponding rear/front port on the same patch panel.

**Why it matters:** Without the cross-mapping, NetBox cannot trace a signal through a patch panel, breaking end-to-end path tracing.

---

## `modules`

**What it checks:** Module bay consistency across three directions:
1. Every installed module must point to a bay that exists on the device.
2. Every module bay that claims to have a module installed must have a module record pointing back to it.
3. The bay's `installed_module` pointer and the module's `module_bay` pointer must agree.

**Why it matters:** Broken module/bay linkage causes the `device-type-drift` check to fail to expand `{module}` placeholder component names, leading to incorrect drift reports.

---

## `macs`

**What it checks:**
1. No MAC address should be assigned to more than one interface across the entire inventory.
2. Any interface with more than one MAC address must have exactly one designated as the primary MAC.

**Why it matters:** Duplicate MACs indicate a data entry error or a device swap that was not fully recorded. Interfaces without a designated primary MAC cannot have DHCP reservations generated for them.

---

## `dhcp-reservations`

**What it checks:** IP addresses with the `dhcp-reserved` status must meet all of the following:
1. Assigned to an interface (not floating).
2. The interface must have a usable MAC address (a single MAC, or a designated primary MAC).
3. The IP must fall within a prefix tagged `dhcp-reserved` (not just any prefix).
4. The IP must not fall within a range tagged `dhcp-pool` (reserved IPs must not overlap the dynamic pool).

**Why it matters:** DHCP reservation records exported to a DHCP server will fail or behave unexpectedly if any of these conditions are violated.

---

## `planned-devices`

**What it checks:** Devices with status `planned` must not show signs of partial physical deployment:
- No cables connected to their interfaces
- No MAC addresses recorded
- No IP addresses assigned to their interfaces

**Why it matters:** A planned device with live data is ambiguously in a half-commissioned state. Either the status should be updated to `active`, or the data should be removed.

---

## `switch-link-symmetry`

**What it checks:** On switch-to-switch cable connections, both ends of the link must have matching configuration:
- Same 802.1Q mode (access/tagged/tagged-all)
- Same native/untagged VLAN
- Same set of tagged VLANs

**Why it matters:** Asymmetric trunk configuration causes VLAN traffic to be silently dropped or mis-routed. This check catches configuration applied to one end of a link but not the other.
