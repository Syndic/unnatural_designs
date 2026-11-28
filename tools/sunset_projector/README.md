# Sunset projector command-line tool

Controls the "Sunset lights" projector from a Mac or a Linux machine: power, colour, brightness,
scenes, effect speed, timers, clock sync, and reading the device's state. It is a thin front end
over [`//libs/sunset_projector`](../../libs/sunset_projector/README.md); the protocol itself is in
that library's [`PROTOCOL.md`](../../libs/sunset_projector/PROTOCOL.md).

## Running it

It needs a machine with a Bluetooth radio, so it runs on the host, not in the devcontainer (Docker
Desktop's VM has no Bluetooth).

- **macOS** stops any process that uses Bluetooth without declaring
  `NSBluetoothAlwaysUsageDescription`, and a binary started from a shell is judged by the app that
  launched it. So the tool runs from inside a minimal `.app` bundle whose `Info.plist` declares
  that key, launched with `open`. The first run asks for Bluetooth permission.
- **Linux** reaches Bluetooth through BlueZ over D-Bus, so the user needs D-Bus access to BlueZ
  (on most distributions, membership of the `bluetooth` group).
