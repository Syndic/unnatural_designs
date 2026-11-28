# Hardware

From teardown photos of one projector, November 2026.

## Boards

- Controller board silkscreen `TKR-671-SUNSET` (the last character is unclear); LED board
  `SUNSET-TKR01`.
- **Bluetooth chip**: SOP-16, marked `2443SHE` / `EQL602`. The marking matches no public part, so
  it is likely custom or re-marked. Beside it: a 16 MHz crystal (Y1) and a meander PCB antenna.
  Pin 1 (the dot) faces Q1/C2; that row fans out to the four MOSFETs, and the opposite row to the
  crystal and antenna.
- **LED drive**: Q1–Q4, marked `A2SHB` (likely SI2302 N-channel MOSFETs), drive one four-die
  **RGBW** LED with a common anode. The connector pads are labelled `G R W B +`, with series
  resistors on the LED board. No command has lit the white die.
- `662K`, likely an XC6206 3.3 V regulator; D2, an indicator LED.
- On the back: a 3-pin shielded **infrared receiver**, which fits the sibling firmware's remote
  handlers; the power leads; a few unlabelled vias. No labelled UART, TM or SWD pads.

## Which chip

The Bluetooth controller is Phyplus's. On connect, macOS's `bluetoothd` logs the link-layer version
exchange:

```
/usr/bin/log show --predicate 'process == "bluetoothd"'   # then grep ReadRemoteVersionInformationCB
```

For the projector it reports `LMPVersion=6 mfgName=1284 LMPSubVersion=520`. Company 1284 (`0x0504`)
is PHYPLUS Inc in systemd's `20-bluetooth-vendor-product.hwdb`. Version 6 means Core 4.0, although
both candidate datasheets claim 5.x, so it doesn't separate them; subversion `0x0208` is the stack
build. The same log carries the device address, which also appears as bytes 2–7 of the
manufacturer data; its first octet is `FF`, so it is vendor-assigned rather than a registered OUI.

The package rules out the PHY6222 and PHY6252 (QFN or SSOP-24). Phyplus's two SOP-16 parts both fit
the board: pin 16 RF (the corner by the antenna), 15 ground, 14 VDD3, 13 and 12 the 16 MHz crystal
(beside Y1), and pins 1–11 GPIO (the row facing the MOSFETs).

- **PHY6230** (SOP-16 part PHY6230SC): 32-bit CPU (the datasheet doesn't name the core), 64 KB ROM,
  8 KB SRAM and **16 KB OTP** at `0x1FFF8000` — no flash, so no firmware updates. Its lack of an
  update service fits the projector best. Pins: 1 P13, 2 P15, 3 P02/SWDIO, 4 P03/SWCLK, 5 P04
  (UART TX in burning mode), 6 P05 (UART RX), 7 P06, 8 P07, 9 P08, 10 P09, 11 P10.
- **PHY6250**: C-SKY CK802 CPU, internal flash, and a ROM bootloader that falls back to a UART
  "RAMRUN" mode when the flash is invalid. Pins: 1 P34, 2 P2, 3 P3, 4 P7, 5 P9, 6 P10, 7 P11,
  8 P14, 9 P15 (mic bias), 10 P18, 11 P20.

Neither matches the sibling firmware's image, which is built for a Cortex-M0 PHY6222 — consistent
with that firmware's different reply checksum. Telling the two apart needs the ROM loader over UART;
see [`firmware.md`](firmware.md).
