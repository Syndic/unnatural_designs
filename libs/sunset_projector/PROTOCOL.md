# Sunset lights BLE protocol

The Bluetooth Low Energy protocol spoken by the "Sunset lights" projector (model `LT-06`), a lamp
that projects a sunset-style colour wash onto a wall and is sold for use with the MeRGBW phone app.
The same protocol family drives the vendor's other products (LED strips, bulbs, ceiling lights),
with per-product differences noted below.

## Sources and confidence

- **The device itself.** Every line marked **verified** was observed on an `LT-06` (firmware `V03`)
  in November 2026, by eye or with a camera measuring the lit wall.
- **MeRGBW for iOS 1.2.3**, by static analysis of the app binary.
- **MeRGBW for Android 1.6.3**, through the notes of
  [launxy/MeRGBW-Linux](https://github.com/launxy/MeRGBW-Linux), which were checked against a
  TG201A LED strip rather than this projector.

Anything not marked verified is what an app sends, not what this device was seen to do. How the
apps, firmware and hardware were analysed, and the details this specification leaves out, are in
[`docs/sunset_projector/`](../../docs/sunset_projector/README.md).

## Discovery

- Advertised name `Sunset lights`; advertised service UUID `3519`.
- Manufacturer data, counting the 2-byte company ID (`0x0000`) as bytes 0–1:
  - bytes 2–7: the device's Bluetooth address;
  - byte 10: **device type**, `5` for this projector;
  - bytes 11–20: model as NUL-padded ASCII, `LT-06`.
- Device types across the family: 1 addressable strip, 2 RGB line, 3 star, 4 handle, **5 sunset**,
  6 bulb, 7 and 8 ceiling.
- Find the device by this advertisement, not by a stored identifier: each phone or computer sees
  its own identifier for the same device.

## Transport

- GATT service `FFF0`. Commands are written to characteristic `FFF3`; replies arrive as
  notifications on `FFF4`.
- Write **with response**, as both apps do. The Android notes say the firmware answers only that
  write type.
- **One connection at a time.** While one phone or computer is connected, no other can control the
  projector, so a controller should connect, send, and disconnect.
- No pairing or bonding is needed. The apps send a bind check (command `0e`) on connect; the
  projector ignores it, and every command works without it (verified).

## Frames

```
55 <cmd> <seq> <len> <payload...> <cs>
len = payload length + 5   (the whole frame)
cs  = ~(sum of all preceding bytes) & 0xff
```

- `seq` is `ff` for a frame that carries a whole payload. A payload too long for one frame is split,
  with `seq` counting 1…n−1 and `ff` marking the last fragment.
- The projector does not check `cs`: a frame with a wrong checksum was accepted and applied
  (verified). Send the correct value anyway, since other products in the family may check it.

### Replies

`56 <cmd> ff <len> <data...> <last>`, echoing the command number. A command that changes state gets
`56 <cmd> ff 06 00 <last>`, where `00` reads as a success code. On this projector `<last>` does not
follow the checksum rule above (it does on the TG201A strip), so treat it as opaque.

## Commands

All verified on the projector unless marked otherwise. Values are hex unless written in words.

- **`00` state query**, empty payload. Reply data:
  - byte 4: power, `01` on, `00` off;
  - byte 5: brightness, 0–100;
  - bytes 6–13: the timer block, exactly as last set by `0a`.
- **`01` power**: `01` on, `00` off.
- **`02` firmware version query**, empty payload. Reply data bytes 4–6 are ASCII, e.g. `V03`.
- **`03` colour**: 3 bytes, `R G B`. Colour changes fade over about 2 seconds. (Per the Android
  notes, strips and ceiling lights take hue and saturation here instead.)
- **`05` brightness**: 1 byte, 0–100. It persists across colour changes.
- **`06` scene**: 1 byte scene ID. Each plays an animated colour scheme:
  - 134 Sunset, 137 Neon lights, 138 Fantasy color, 139 Sunrise, 140 Forest, 141 Ghost, 143 Aurora,
    144 Dating, 149 Disco, 150 Alarm — the IDs the iOS app offers for this product. All but 139,
    149 and 150 were verified.
  - The generic mode IDs other products use (1, 5, 10) are acknowledged but do nothing.
- **`0a` timers**: 8 bytes, `onEnabled onHour onMinute onDays offEnabled offHour offMinute offDays`.
  - Days is a 7-bit mask, **Monday first**: bit 0 Monday … bit 6 Sunday.
  - Firing was verified for all days (`7f`), for one day (`08`, Thursday on a Thursday), and for
    not firing on the excluded day (`77` on a Thursday).
  - All zeros clears both timers.
- **`0c` clock**: 8 bytes, `year(u16 big-endian) month day hour minute second weekday`. Send it
  before setting timers; the timers run on this clock. Weekday numbering: see open questions.
- **`0f` effect speed**: 1 byte. 100 is fast, 10 is slow, and the default appears to be 100.

### Silent on this projector

No reply and no visible effect:

- `07` music mode and `08` music sensitivity. Music sync for this product happens in the phone app,
  which listens through the microphone and streams `03` colour frames.
- `0e` bind check.
- `10` (white on/off for bulbs; laser brightness in the iOS app), `11` (white brightness for bulbs;
  motor speed in the iOS app), and `12` (cold/warm white for ceiling lights, 4 bytes
  `coldBrightness warmBrightness coldPercent warmPercent`), each tried with real payloads.
- Every command from `13` to `ef`, sent with an empty payload.

### Not to send

These write persistent or factory settings in the vendor's other firmware, and were left untested:

- `0b` LED count (u16 big-endian) and `0d` configuration mode.
- `f0`–`ff`, the factory area (remote-key tables, vendor information, colour limits).

### Used by other products only

- `04` per-segment colours and `09` custom ("DIY") colour sequences, both split across fragments.

## The white LED

The projector's LED is RGBW, but nothing found drives the white die. The apps only ever send white
commands to bulbs (type 6), no command tried lit it, and the firmware may not drive it at all.

## Open questions

- **Weekday in `0c`.** The iOS app numbers weekdays from Sunday (1 = Sunday), the Android app from
  Monday (1 = Monday). Timers fired correctly with iOS numbering, so the projector either uses it or
  ignores the byte and derives the weekday from the date. Sending a deliberately wrong weekday would
  tell them apart.
- **Brightness offset.** The Android notes read this product's brightness as reply byte 5 minus 5,
  but setting 100 reads back `64` (100).

## Hardware

- Phyplus Bluetooth chip: the link-layer version exchange reports company ID `0x0504` (PHYPLUS).
  The 16-pin package and board layout fit a PHY6230 or PHY6250.
- One RGBW LED driven by four MOSFETs, one per colour; an infrared receiver, which neither app uses;
  and a 3.3 V regulator.
- No firmware update path: the projector exposes no update service, and neither app carries
  firmware for it. Reading the firmware out would need the chip's serial bootloader.

## Worked frames

Each verified on the projector:

- Power on `55 01 ff 06 01 a3`; power off `55 01 ff 06 00 a4`.
- Red `55 03 ff 08 ff 00 00 a1`, green `55 03 ff 08 00 ff 00 a1`, blue `55 03 ff 08 00 00 ff a1`,
  white `55 03 ff 08 ff ff ff a3`, black `55 03 ff 08 00 00 00 a0`.
- Brightness 10 `55 05 ff 06 0a 96`, 40 `55 05 ff 06 28 78`, 100 `55 05 ff 06 64 3c`.
- State query `55 00 ff 05 a6`; version query `55 02 ff 05 a4`.
