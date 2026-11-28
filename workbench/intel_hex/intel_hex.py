"""Intel HEX firmware images: list segments, flatten to binary, wrap as an ARM ELF, or compare.

Usage:
  intel_hex.py segments IMAGE.hex
  intel_hex.py bin IMAGE.hex OUT.bin     flat image from lowest to highest address, gaps 0xFF
  intel_hex.py elf IMAGE.hex OUT.elf     ELF32 little-endian ARM, one PT_LOAD per segment
  intel_hex.py compare REF.hex OTHER.hex ...   segments, differing addresses, and strings
"""

import re
import struct
import sys


def load(path):
    """Read an image as (address -> byte, start address or None).

    Honours extended segment (2) and linear (4) address records, takes the start address from a
    start segment (3) or start linear (5) record, and refuses a record whose checksum is wrong.
    """
    mem, base, start = {}, 0, None
    with open(path) as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line.startswith(":"):
                continue
            rec = bytes.fromhex(line[1:])
            if len(rec) < 5 or len(rec) != rec[0] + 5 or sum(rec) & 0xFF:
                sys.exit(f"{path}:{lineno}: malformed record or bad checksum")
            addr, typ, data = int.from_bytes(rec[1:3], "big"), rec[3], rec[4 : 4 + rec[0]]
            if typ == 0:
                for i, b in enumerate(data):
                    mem[base + addr + i] = b
            elif typ == 2:
                base = int.from_bytes(data, "big") << 4
            elif typ == 3:
                start = (int.from_bytes(data[:2], "big") << 4) + int.from_bytes(data[2:], "big")
            elif typ == 4:
                base = int.from_bytes(data, "big") << 16
            elif typ == 5:
                start = int.from_bytes(data, "big")
    if not mem:
        sys.exit(f"{path}: no data records")
    return mem, start


def segments(mem):
    """Contiguous address ranges as (first, last) pairs, inclusive."""
    addrs = sorted(mem)
    out, start, prev = [], addrs[0], addrs[0]
    for a in addrs[1:]:
        if a != prev + 1:
            out.append((start, prev))
            start = a
        prev = a
    out.append((start, prev))
    return out


def strings(mem):
    found = []
    for lo, hi in segments(mem):
        blob = bytes(mem[a] for a in range(lo, hi + 1))
        found += [m.group().decode() for m in re.finditer(rb"[ -~]{5,}", blob)]
    return found


def to_elf(mem, entry):
    """ELF image; the entry point is the start address, else the lowest loaded address."""
    segs = segments(mem)
    ehdr_size, phdr_size = 52, 32
    off = ehdr_size + phdr_size * len(segs)
    phdrs, blobs = b"", b""
    for s, e in segs:
        data = bytes(mem[a] for a in range(s, e + 1))
        # RWX, so an analyser treats code that runs from SRAM as code.
        phdrs += struct.pack("<IIIIIIII", 1, off + len(blobs), s, s, len(data), len(data), 7, 4)
        blobs += data
    ident = b"\x7fELF" + bytes([1, 1, 1, 0]) + b"\0" * 8
    # e_type EXEC, e_machine ARM (40), EABI version 5 flags.
    ehdr = ident + struct.pack(
        "<HHIIIIIHHHHHH",
        2, 40, 1, segs[0][0] if entry is None else entry, ehdr_size, 0, 0x05000000, ehdr_size,
        phdr_size, len(segs), 40, 0, 0,
    )  # fmt: skip
    return ehdr + phdrs + blobs


def describe(name, mem):
    segs = segments(mem)
    shown = ", ".join(f"{lo:08x}-{hi:08x}" for lo, hi in segs[:6])
    more = " ..." if len(segs) > 6 else ""
    print(f"== {name}: {len(mem)} bytes in {len(segs)} segments: {shown}{more}")


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    cmd, args = argv[0], argv[1:]
    if cmd == "segments":
        mem, start = load(args[0])
        if start is not None:
            print(f"start address 0x{start:08x}")
        for s, e in segments(mem):
            print(f"0x{s:08x}-0x{e:08x} ({e - s + 1} bytes)")
    elif cmd == "bin":
        mem, _ = load(args[0])
        lo, hi = min(mem), max(mem)
        with open(args[1], "wb") as f:
            f.write(bytes(mem.get(a, 0xFF) for a in range(lo, hi + 1)))
        print(f"wrote 0x{lo:08x}..0x{hi:08x} to {args[1]}")
    elif cmd == "elf":
        mem, start = load(args[0])
        with open(args[1], "wb") as f:
            f.write(to_elf(mem, start))
        for s, e in segments(mem):
            print(f"PT_LOAD 0x{s:08x} size {e - s + 1}")
    elif cmd == "compare":
        images = [(p, load(p)[0]) for p in args]
        ref_name, ref = images[0]
        ref_strings = set(strings(ref))
        describe(ref_name, ref)
        for name, mem in images[1:]:
            describe(name, mem)
            diff = sorted(a for a in set(mem) | set(ref) if mem.get(a) != ref.get(a))
            first = hex(diff[0]) if diff else "none"
            print(f"   differs from {ref_name} at {len(diff)} addresses; first {first}")
            here = strings(mem)
            print(f"   strings only here: {[s for s in here if s not in ref_strings][:40]}")
            print(f"   strings only in {ref_name}: {sorted(ref_strings - set(here))[:40]}")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
