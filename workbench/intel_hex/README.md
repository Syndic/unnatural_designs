# intel_hex

`intel_hex.py` works on firmware images in Intel HEX format (often `.hex` or `.hex16`). It uses
only the standard library, so it runs anywhere, including the devcontainer.

```bash
python3 -I workbench/intel_hex/intel_hex.py segments IMAGE.hex
python3 -I workbench/intel_hex/intel_hex.py bin IMAGE.hex OUT.bin
python3 -I workbench/intel_hex/intel_hex.py elf IMAGE.hex OUT.elf
python3 -I workbench/intel_hex/intel_hex.py compare REF.hex OTHER.hex...
```

- `segments` lists the contiguous address ranges the image loads.
- `bin` writes a flat image from the lowest to the highest address, filling gaps with `0xFF`.
- `elf` writes a minimal 32-bit little-endian ARM ELF with one `PT_LOAD` segment per range, marked
  read-write-execute so an analyser treats code that runs from SRAM as code. This is the form
  [Ghidra](../ghidra/README.md) imports. For a non-ARM chip, change `e_machine` (40).
- `compare` reports, for each image against the first, how many addresses differ and which
  printable strings appear in only one of them.

No automated tests. Last checked by hand on 2026-11-27: `bin` and `elf` on the iOS MeRGBW app's
bundled firmware reproduced, byte for byte, the files the original analysis used, and `compare`
ran across the Android app's three images.
