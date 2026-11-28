# Firmware

Neither app carries firmware for the projector. The images they do carry belong to sibling
products that speak the same protocol family, and are useful for that reason. Hashes are in the
[README](README.md).

## The iOS app's bundled image

`simpleBlePeripheral.hex16` is Intel HEX loading at `0x1102xxxx`, which is Phyplus PHY62xx address
space.

### Decompiling it

- Convert the HEX to a minimal 32-bit little-endian ARM ELF with one `PT_LOAD` segment per
  contiguous address range, marked read-write-execute so the analyser treats SRAM-resident code as
  code.
- Import that ELF into [Ghidra](https://ghidra-sre.org) 12.2 headless with the language
  `ARM:LE:32:Cortex`, and run a post-script that decompiles every function to one C file. The image
  has 793 functions.
- Ghidra 12.2 needs JDK 25; JDK 21 is refused.
- Ghidra's release ships no Linux arm64 decompiler. Build it in the Ghidra tree with
  `support/gradle/gradlew buildNatives`, then copy `build/os/linux_arm_64` to `os/linux_arm_64`.
  The build needs `bison` and `flex`.

### What it is

A sibling product, not the projector:

- It advertises service `3519` with the name `DYWL-Light`, uses the same `FFF0`/`FFF3`/`FFF4`
  table, and rejects frames whose first byte isn't `55`.
- It drives an addressable strip over SPI with DMA, with `led_number` capped at 300, and has
  infrared-remote handlers.
- Its replies are `56 <cmd> <seq> 06 <status> <~sum>`, with a correct checksum. The projector's
  final reply byte doesn't follow that rule, so the projector runs different firmware.
- Commands it handles (`0b` and up; `00`–`0a` sit in a switch table Ghidra didn't decode):
  - `0b` LED count (u16 big-endian, at most 300);
  - `0c` clock (year u16 big-endian, month, day, weekday, hour, minute, second — weekday before the
    time, unlike the apps' order);
  - `0d` test mode;
  - `0e` bind: the payload is a 6-byte "app MAC", matched against 3 stored slots, answered with
    `0e` on a match and `1e` otherwise;
  - `0f` effect speed, stored as value / 10;
  - `f1` key mode; `f2` remote-key table; `f3` vendor information; `f4` "linear"; `f5` colour
    maximum (2 bytes); `f6` a flag plus a 1-second timer.

## The Android app's images

Three images, for the TG201, TG609 and TG610 strips, delivered over the Phyplus OTA service (see
[`android_app.md`](android_app.md)). They have not been decompiled.

## Reading the projector's own firmware

Nothing over the air can do it: the projector exposes only `FFF0`, no OTA service, and no command
reads memory. It needs the chip's serial bootloader, which depends on which chip it is (see
[`hardware.md`](hardware.md)):

- **PHY62x2 family**: holding the TM pin high at power-up enters the ROM's UART loader.
  `rdwr_phy62x2.py` from [pvvx/THB2](https://github.com/pvvx/THB2) talks to it, with `read_chip`
  and read-all-flash commands, over a 3.3 V USB-serial adapter.
- **PHY6230 or PHY6250**, the two parts that fit this board: both bring the loader's UART out on
  SOP-16 pins 5 and 6. `rdwr_phy62x2.py`'s revision query reports `6230` for a PHY6230, which would
  settle the identity before any read.
- A PHY6230 holds its program in 16 KB of one-time-programmable memory rather than flash, which
  would explain why the projector has no update path.

Before opening anything, an FCC ID on the label would lead to the FCC filing, whose internal photos
usually show the chip marking.
