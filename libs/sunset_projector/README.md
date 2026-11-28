# Sunset projector library

Controls the "Sunset lights" projector (model `LT-06`) over Bluetooth Low Energy. The command-line
tool in [`//tools/sunset_projector`](../../tools/sunset_projector/README.md) and the iOS/iPadOS app
in [`//apps/sunset_projector`](../../apps/sunset_projector/README.md) both build on it.

[`PROTOCOL.md`](PROTOCOL.md) is the specification this library implements: discovery, frame
format, every command, and which parts were verified on the device.

## Responsibilities

- **Protocol**: building command frames and parsing replies, with no Bluetooth code, so it can be
  tested anywhere.
- **Transport**: finding the projector by its advertisement, connecting, writing to `FFF3` and
  reading notifications from `FFF4`. Each operating system reaches Bluetooth through its own
  interface — CoreBluetooth on macOS and iOS, BlueZ on Linux — so this part is per platform, behind
  one interface.
- **Connection discipline**: the projector accepts one connection at a time, so operations connect,
  send, and disconnect rather than holding the connection open.
