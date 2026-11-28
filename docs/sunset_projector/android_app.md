# MeRGBW for Android

The vendor's APK (`com.mergbw.android`), decompiled with [jadx](https://github.com/skylot/jadx).
Its manifest's string pool carries the version string `1.6.8`. Hashes and source are in the
[README](README.md). Android packages aren't encrypted, so all of its code and assets are
readable.

[launxy/MeRGBW-Linux](https://github.com/launxy/MeRGBW-Linux) is an independent Linux rewrite of
Android MeRGBW 1.6.3. Its `mergbw-linux/PROTOCOL.md` documents the protocol as seen from a TG201A
LED strip; the parts relevant to the projector are folded into
[`PROTOCOL.md`](../../libs/sunset_projector/PROTOCOL.md).

## Device identity

While scanning, the app reads byte 10 of a light's manufacturer data as its device type and bytes
11–20 as its model code. It names five types:

| Type | Name in the app | Translation |
|---|---|---|
| 1 | 幻彩灯带 | "dream-colour" addressable LED strip |
| 2 | RGB灯条 | RGB light bar |
| 3 | 星空灯 | starry-sky projector |
| 5 | 夕阳灯 | sunset lamp |
| 6 | 幻彩灯泡 | "dream-colour" bulb |

MeRGBW-Linux adds 4 (handle) and 7/8 (ceiling lights). Decoded this way, the projector's
advertisement reads type 5, model `LT-06`, and a factory ID of 16.

## Firmware update path

- **Only type 1 is ever checked.** For a type 1 device reporting firmware below `V04`, the app
  marks an update available. Every other type skips the check and reports the current version as
  the latest.
- **The update screen is hidden**: on the device-information screen it opens only after three taps
  within half a second.
- **The image is chosen by model code**: TG201, TG609 and TG610 map to
  `simpleBlePeripheral-<model>.hex16`; any other model fails with an error.
- **Transport**: the Phyplus OTA service `5833ff01`, with characteristics `ff02`–`ff04`.

So the projector fails twice over: type 5 is never checked, and `LT-06` has no image. It also
exposes no `5833ff01` service.
