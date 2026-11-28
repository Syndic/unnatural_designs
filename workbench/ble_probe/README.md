# ble_probe

Three small CoreBluetooth programs for finding and talking to a Bluetooth Low Energy device from a
Mac. They need the Mac's radio, so they run on the macOS host (Docker Desktop's VM has no
Bluetooth), each from its own app bundle (see the [workbench README](../README.md)).

- **`BLEScan`** (`scan.swift`): a passive scan. It prints a table of what it heard and writes
  every advertisement — name, signal strength, manufacturer data, services — to JSON.
- **`BLEGatt`** (`gatt.swift`): connects to one device and dumps its services, characteristics and
  descriptors, reading every readable value and briefly subscribing to notifications. A readable
  characteristic is subscribed only after its read returns, so a notification is never mistaken for
  the read value. It never writes.
- **`BLEPoke`** (`poke.swift`): connects, subscribes to a notify characteristic, and runs a script
  of writes and pauses against a write characteristic, logging every notification. Writes go out
  with response, one at a time, waiting up to 5 s for each acknowledgement. It stops rather than
  carry on if the notify subscription fails (every reply would be lost and read as silence) or an
  acknowledgement doesn't come (a late one couldn't be told apart from the next frame's).

No automated tests. Last checked by hand on 2026-11-27: all three built, and `BLEScan` listed the
devices in range. `BLEGatt` and `BLEPoke` drove the Sunset projector throughout its investigation,
but their current versions (argument checking, configurable UUIDs) have not yet reached a device.

## Building

On the macOS host, with Xcode or its command-line tools:

```bash
workbench/ble_probe/build.sh <outdir>
```

That writes `BLEScan.app`, `BLEGatt.app` and `BLEPoke.app` into `<outdir>`. Each asks for Bluetooth
permission on its first run.

## Running

Launch each with `open`, so macOS attributes it to its own bundle:

```bash
open -W -n --stdout <log> --stderr <log> <outdir>/BLEScan.app --args [seconds] [out.json]
open -W -n --stdout <log> --stderr <log> <outdir>/BLEGatt.app --args <device-id> <out.txt>
open -W -n --stdout <log> --stderr <log> <outdir>/BLEPoke.app \
  --args [--service UUID] [--write UUID] [--notify UUID] <device-id> <out.txt> <step>...
```

- `<device-id>` is the identifier macOS assigned the device, from `BLEScan`'s JSON. It is specific
  to this Mac; other computers see a different one for the same device.
- A `BLEPoke` step is a frame in hex (`"55 01 ff 06 01 a3"`) or a pause (`sleep:<seconds>`).
  `--service`, `--write` and `--notify` default to `FFF0`, `FFF3` and `FFF4`, a layout common on
  inexpensive BLE light controllers.
- `BLEPoke`'s log lines (`-> <uuid>: <hex>` for each write, `<- <uuid>: <hex>` for each
  notification) are timestamped relative to its start, for lining up with `light_meter`.

Many cheap devices accept one connection at a time: while a phone app holds the device, a probe
can't see or reach it.
