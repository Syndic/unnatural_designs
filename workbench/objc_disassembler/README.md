# objc_disassembler

`objcdis.py` lists and disassembles the Objective-C methods of an arm64 Mach-O binary (an iOS or
macOS app's executable), annotating each instruction with the selectors, strings and imported
symbols it references. It reads the binary directly, so it needs no Xcode and runs in the
devcontainer.

No automated tests. Last checked by hand on 2026-11-27 against MeRGBW for iOS 1.2.3: `dis` over
`LEDCommandAnalysislogic` reproduced the original analysis output exactly, `classes` and `callers`
ran with nothing skipped, and every class's class methods were listed.

## Running

It needs [LIEF](https://lief.re) and [Capstone](https://www.capstone-engine.org), which are not
project dependencies; install them into a throwaway virtualenv inside the devcontainer:

```bash
python3 -m venv /tmp/objcdis-venv && /tmp/objcdis-venv/bin/pip install lief capstone
/tmp/objcdis-venv/bin/python -I workbench/objc_disassembler/objcdis.py BINARY classes 'Manager$'
```

The subcommands are listed in the script's docstring: list a class's methods, disassemble methods
by selector, find the methods that send or load a selector, scan all code for sends of a selector,
disassemble at an address, and decode `CFString` constants. Anything it cannot parse is reported on
stderr rather than silently dropped.

## What it has to handle

Recent Xcode output differs from what older Mach-O tools expect:

- **Chained fixups.** Pointers in the data segments are stored as chained-fixup entries. A rebase
  entry's target is `raw & 0xFFFFFFFFF`, plus the image base when it is below it; an entry with
  bit 63 set is a bind, resolved through the imports instead; and a zero is a null pointer, not the
  image base.
- **Relative method lists.** Method lists use 32-bit offsets relative to each field rather than
  absolute pointers (flag `0x80000000` in the list header).
- **Selector stubs.** Calls go through `__objc_stubs`, small thunks that load a selector reference
  and branch to `objc_msgSend`; resolving each stub's selector gives readable call sites.
- **Categories.** Methods added by categories are listed in `__objc_catlist`, not in the class's
  own method list; they appear as classes named `CAT:<category>`.

## Limits

- arm64 only; for a universal binary it takes the arm64 slice.
- Objective-C metadata only: pure Swift classes and functions show up as unnamed code at best.
- App Store binaries are FairPlay-encrypted. It reads them as installed, which works only when the
  encrypted range (`cryptsize` in `LC_ENCRYPTION_INFO_64`) is small or the binary was decrypted.
