# Network management tools

This repository contains tools for managing a home network modelled in [NetBox](https://netboxlabs.com/products/netbox/) and controlled by [UniFi Network](https://unifi.ui.com/).

## Tools

### `cmd/netbox_audit` — NetBox audit

Validates the NetBox model for internal consistency. Runs 18 configurable checks covering device fields, cabling, IP addressing, VRF assignment, PoE power, DHCP reservations, module bay linkage, and more. Produces a human-readable or JSON report.

→ See [`cmd/netbox_audit/README.md`](cmd/netbox_audit/README.md)

### `..............` — Netbox/Unifi drift detection

Compares the intended network state in NetBox against the live state reported by the UniFi controller. Reports devices, clients, and configuration that exist in one source but not the other, or that disagree between them.

### `...............` — NetBox → UniFi sync

Reads all configuration data from Netbox that can be automatically imported into UniFi (Currently DHCP reservations with optional DNS entries, and honeypot IP addresses) and pushes it to the UniFi controller.
