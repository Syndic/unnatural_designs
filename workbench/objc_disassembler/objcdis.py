"""Annotated arm64 disassembly of ObjC methods in a Mach-O, resolving selector stubs and strings.

Usage:
  objcdis.py BIN classes CLASS_REGEX                   list the methods of matching classes
  objcdis.py BIN dis CLASS SELECTOR_REGEX              disassemble matching methods of one class
  objcdis.py BIN callers SELECTOR_REGEX [CLASS_REGEX]  methods that send a matching selector
  objcdis.py BIN selusers SELECTOR_REGEX [CLASS_REGEX] methods that load a matching selector
  objcdis.py BIN scanall SELECTOR_REGEX                every send of a matching selector in __text
  objcdis.py BIN addr HEX_ADDR ...                     disassemble the functions at addresses
  objcdis.py BIN cfstr HEX_ADDR ...                    decode CFString constants at addresses

Anything it cannot parse is reported on stderr and skipped.
"""

import re
import sys

import capstone
import lief

lief.logging.disable()
binpath, cmd = sys.argv[1], sys.argv[2]
fat = lief.MachO.parse(binpath)
b = fat.take(lief.MachO.Header.CPU_TYPE.ARM64) if fat.size > 1 else fat.at(0)
BASE = b.imagebase


def skipped(what, err):
    print(f"skipped {what}: {err}", file=sys.stderr)


def mem(addr, n):
    return bytes(b.get_content_from_virtual_address(addr, n))


def ptr(addr):
    raw = int.from_bytes(mem(addr, 8), "little")
    if raw == 0 or raw >> 63:  # null, or a bind to an imported symbol
        return None
    tgt = raw & 0xFFFFFFFFF
    return tgt + BASE if tgt < BASE else tgt


def cstr(addr, limit=200):
    try:
        raw = mem(addr, limit)
    except Exception:
        return "?"
    return raw.split(b"\0", 1)[0].decode("utf-8", "replace")


sections = {(s.segment_name, s.name): s for s in b.sections}


def in_sect(addr, seg, name):
    s = sections.get((seg, name))
    return s is not None and s.virtual_address <= addr < s.virtual_address + s.size


def describe(addr):
    if in_sect(addr, "__TEXT", "__cstring") or in_sect(addr, "__TEXT", "__objc_methname"):
        return repr(cstr(addr))
    for seg in ("__DATA", "__DATA_CONST"):
        if in_sect(addr, seg, "__cfstring"):
            p = ptr(addr + 16)
            return "@" + repr(cstr(p)) if p else None
        if in_sect(addr, seg, "__objc_selrefs"):
            p = ptr(addr)
            return "sel:" + cstr(p) if p else None
        if in_sect(addr, seg, "__objc_classrefs"):
            p = ptr(addr)
            return f"classref:{classnames.get(p, hex(p) if p else 'extern')}"
    if in_sect(addr, "__TEXT", "__const"):
        return "const:" + mem(addr, 16).hex()
    return None


md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
md.detail = True

stubs = {}
st = sections.get(("__TEXT", "__objc_stubs"))
if st:
    data = mem(st.virtual_address, st.size)
    for off in range(0, st.size, 32):
        ins = list(md.disasm(data[off : off + 8], st.virtual_address + off))
        if len(ins) == 2 and ins[0].mnemonic == "adrp" and ins[1].mnemonic == "ldr":
            page = ins[0].operands[1].imm
            selref = page + ins[1].operands[1].mem.disp
            p = ptr(selref)
            if p:
                stubs[st.virtual_address + off] = cstr(p)

syms = {s.value: s.name for s in b.symbols if s.value}
stub_sect = sections.get(("__TEXT", "__stubs"))
bindings = {}
for bi in b.bindings:
    if bi.has_symbol:
        bindings[bi.address] = bi.symbol.name
if stub_sect:
    data = mem(stub_sect.virtual_address, stub_sect.size)
    for off in range(0, stub_sect.size, 12):
        ins = list(md.disasm(data[off : off + 8], stub_sect.virtual_address + off))
        if len(ins) == 2 and ins[0].mnemonic == "adrp":
            got = ins[0].operands[1].imm + ins[1].operands[1].mem.disp
            if got in bindings:
                syms[stub_sect.virtual_address + off] = bindings[got]

classnames = {}


class M:
    def __init__(self, name, address, types, inst):
        self.name, self.address, self.mangled_type, self.is_instance = name, address, types, inst


class C:
    def __init__(self, name, methods):
        self.name, self.methods = name, methods


def i32(a):
    return int.from_bytes(mem(a, 4), "little", signed=True)


def methods_of(ro, inst):
    ml = ptr(ro + 32)
    out = []
    if not ml:
        return out
    ef = int.from_bytes(mem(ml, 4), "little")
    cnt = int.from_bytes(mem(ml + 4, 4), "little")
    if ef & 0x80000000:
        for i in range(cnt):
            e = ml + 8 + i * 12
            selref = e + i32(e)
            sp = ptr(selref)
            name = cstr(sp) if sp else "?"
            types = cstr(e + 4 + i32(e + 4))
            imp = e + 8 + i32(e + 8)
            out.append(M(name, imp, types, inst))
    else:
        es = ef & 0xFFFC
        for i in range(cnt):
            e = ml + 8 + i * es
            out.append(M(cstr(ptr(e)), ptr(e + 16), cstr(ptr(e + 8)), inst))
    return out


classes = []
cl = sections.get(("__DATA_CONST", "__objc_classlist")) or sections.get(
    ("__DATA", "__objc_classlist")
)
for off in range(0, cl.size, 8):
    c = ptr(cl.virtual_address + off)
    if not c:
        continue
    ro = (ptr(c + 32) or 0) & ~7
    if not ro:
        continue
    name = cstr(ptr(ro + 24))
    classnames[c] = name
    try:
        ms = methods_of(ro, True)
    except Exception as e:
        skipped(f"instance methods of {name}", e)
        ms = []
    try:
        meta_c = ptr(c)
        mro = (ptr(meta_c + 32) or 0) & ~7 if meta_c else 0
        ms += methods_of(mro, False) if mro else []
    except Exception as e:
        skipped(f"class methods of {name}", e)
    classes.append(C(name, ms))

cat = sections.get(("__DATA_CONST", "__objc_catlist")) or sections.get(("__DATA", "__objc_catlist"))
for off in range(0, cat.size if cat else 0, 8):
    try:
        c = ptr(cat.virtual_address + off)
        # category_t: name, cls, instanceMethods @16, classMethods @24.
        # methods_of reads its list at ro+32, hence the -32.
        ms = methods_of(c + 16 - 32, True) + methods_of(c + 24 - 32, False)
        classes.append(C("CAT:" + cstr(ptr(c)), ms))
    except Exception as e:
        skipped(f"category at catlist+{off:#x}", e)

starts = sorted(set(b.function_starts.functions)) if b.has_function_starts else []
starts = [s + BASE if s < BASE else s for s in starts]


def func_end(addr):
    import bisect

    i = bisect.bisect_right(starts, addr)
    return starts[i] if i < len(starts) else addr + 0x400


def disasm(addr):
    end = func_end(addr)
    code = mem(addr, end - addr)
    regs = {}
    for ins in md.disasm(code, addr):
        note = ""
        ops = ins.operands
        if ins.mnemonic == "adrp":
            regs[ops[0].reg] = ops[1].imm
        elif (
            ins.mnemonic == "add"
            and len(ops) == 3
            and ops[2].type == capstone.arm64.ARM64_OP_IMM
            and ops[1].reg in regs
        ):
            a = regs[ops[1].reg] + ops[2].imm
            regs[ops[0].reg] = a
            note = describe(a) or hex(a)
        elif (
            ins.mnemonic == "ldr"
            and len(ops) == 2
            and ops[1].type == capstone.arm64.ARM64_OP_MEM
            and ops[1].mem.base in regs
        ):
            a = regs[ops[1].mem.base] + ops[1].mem.disp
            note = describe(a) or hex(a)
        elif ins.mnemonic in ("bl", "b") and ops and ops[0].type == capstone.arm64.ARM64_OP_IMM:
            t = ops[0].imm
            note = ("-> [" + stubs[t] + "]") if t in stubs else syms.get(t, "")
        elif (
            ins.mnemonic in ("mov", "movz", "movk", "orr")
            and ops
            and ops[-1].type == capstone.arm64.ARM64_OP_IMM
        ):
            v = ops[-1].imm
            note = f"#{v} = 0x{v & 0xFFFFFFFFFFFFFFFF:x}" if v > 9 else ""
        print(
            f"  {ins.address:x}: {ins.mnemonic:6} {ins.op_str:34} {('; ' + note) if note else ''}"
        )


if cmd == "classes":
    rx = re.compile(sys.argv[3])
    for c in classes:
        if rx.search(c.name):
            print(f"== {c.name}")
            for m in c.methods:
                print(
                    f"  {'-' if m.is_instance else '+'}{m.name}  @{m.address:x}  {m.mangled_type}"
                )
elif cmd == "dis":
    cls, rx = sys.argv[3], re.compile(sys.argv[4])
    for c in classes:
        if c.name == cls:
            for m in c.methods:
                if rx.search(m.name):
                    print(f"\n### {'-' if m.is_instance else '+'}[{cls} {m.name}] @{m.address:x}")
                    disasm(m.address)

if cmd == "callers":
    rx = re.compile(sys.argv[3])
    targets = {a: s for a, s in stubs.items() if rx.search(s)}
    crx = re.compile(sys.argv[4]) if len(sys.argv) > 4 else None
    for c in classes:
        if crx and not crx.search(c.name):
            continue
        for m in c.methods:
            if not m.address:
                continue
            try:
                code = mem(m.address, func_end(m.address) - m.address)
            except Exception as e:
                skipped(f"body of {c.name} {m.name}", e)
                continue
            for ins in md.disasm(code, m.address):
                if ins.mnemonic == "bl" and ins.operands[0].imm in targets:
                    sign = "-" if m.is_instance else "+"
                    print(f"{c.name} {sign}{m.name} -> {targets[ins.operands[0].imm]}")

if cmd == "addr":
    for a in sys.argv[3:]:
        print(f"\n### block @{a}")
        disasm(int(a, 16))

if cmd == "selusers":
    # methods that load a matching selector reference (target/action, performSelector, ...)
    rx = re.compile(sys.argv[3])
    crx = re.compile(sys.argv[4]) if len(sys.argv) > 4 else None
    for c in classes:
        if crx and not crx.search(c.name):
            continue
        for m in c.methods:
            if not m.address:
                continue
            try:
                code = mem(m.address, func_end(m.address) - m.address)
            except Exception as e:
                skipped(f"body of {c.name} {m.name}", e)
                continue
            regs = {}
            for ins in md.disasm(code, m.address):
                ops = ins.operands
                if ins.mnemonic == "adrp":
                    regs[ops[0].reg] = ops[1].imm
                elif ins.mnemonic in ("ldr", "add") and len(ops) >= 2:
                    base = (
                        ops[1].mem.base
                        if ops[1].type == capstone.arm64.ARM64_OP_MEM
                        else (ops[1].reg if ins.mnemonic == "add" else None)
                    )
                    if base in regs:
                        a = regs[base] + (
                            ops[1].mem.disp
                            if ops[1].type == capstone.arm64.ARM64_OP_MEM
                            else (
                                ops[2].imm
                                if len(ops) > 2 and ops[2].type == capstone.arm64.ARM64_OP_IMM
                                else 0
                            )
                        )
                        d = describe(a)
                        if d and d.startswith("sel:") and rx.search(d[4:]):
                            print(f"{c.name} {'-' if m.is_instance else '+'}{m.name} loads {d}")

if cmd == "scanall":
    # every bl in __text to a selector stub matching the regex, with the enclosing function start
    import bisect

    rx = re.compile(sys.argv[3])
    targets = {a: s for a, s in stubs.items() if rx.search(s)}
    tx = sections[("__TEXT", "__text")]
    data = mem(tx.virtual_address, tx.size)
    md.detail = False
    for off in range(0, tx.size, 4):
        w = int.from_bytes(data[off : off + 4], "little")
        if w >> 26 == 0b100101:  # BL imm26
            imm = w & 0x3FFFFFF
            if imm & 0x2000000:
                imm -= 1 << 26
            pc = tx.virtual_address + off
            t = pc + imm * 4
            if t in targets:
                i = bisect.bisect_right(starts, pc) - 1
                print(f"bl @{pc:x} in func @{starts[i]:x} -> {targets[t]}")

if cmd == "cfstr":
    # decode CFString structs (UTF-16 or ASCII) at the given addresses
    for a in sys.argv[3:]:
        addr = int(a, 16)
        flags = int.from_bytes(mem(addr + 8, 8), "little")
        p, n = ptr(addr + 16), int.from_bytes(mem(addr + 24, 8), "little")
        raw = mem(p, n * 2 if flags & 0x4 == 0 and flags != 0x7C8 else n)
        try:
            s = raw.decode("utf-16le") if (flags & 0xFF) == 0xD0 else raw.decode("utf-8", "replace")
        except Exception:
            s = raw.hex()
        print(f"{a}: flags=0x{flags:x} len={n} {s!r}")
