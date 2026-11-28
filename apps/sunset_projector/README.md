# Sunset projector app

An iOS and iPadOS app for controlling the "Sunset lights" projector: power, colour, brightness,
scenes, effect speed and timers. It builds on
[`//libs/sunset_projector`](../../libs/sunset_projector/README.md), whose
[`PROTOCOL.md`](../../libs/sunset_projector/PROTOCOL.md) specifies what goes over the air.

## Constraints that shape it

- **Several devices share one projector.** The projector accepts one Bluetooth connection at a
  time, so the app connects, sends, and disconnects, and reports when another device holds the
  connection rather than failing silently.
- **Discovery is by advertisement.** iOS gives each device its own identifier for the same
  projector, so the app finds projectors by what they advertise (device type 5, model `LT-06`)
  rather than by a stored identifier.
- **Bluetooth permission.** The app's `Info.plist` declares `NSBluetoothAlwaysUsageDescription`;
  iOS stops an app that uses Bluetooth without it.
- **Building and installing need macOS and Xcode**, so this package builds on the host rather than
  in the devcontainer. An app signed with a free Apple developer account stops launching after
  seven days and must be reinstalled; a paid account's distribution profiles last much longer.
