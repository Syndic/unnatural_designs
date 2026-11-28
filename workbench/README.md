# Workbench

Tools built to help develop what this repository does release — reverse-engineering a device,
checking a physical result, poking at a protocol — and kept because the next investigation will
want them. They are not products: nothing here is released, and each tool is held to a lighter bar
than `//tools/`.

## The contract

- **Nothing released depends on the workbench.** Code under `apps/`, `libs/`, `tools/` and
  `services/` never imports, calls or builds anything here. That is what lets the rest of these
  rules be looser.
- **Each tool has a README** saying what it does, how to run it, and how it was last checked. Tests
  are optional, and a tool without them says so.
- **Lint still applies.** CI runs `ruff` over the whole tree and `shellcheck` over every `*.sh`, so
  workbench code passes both. `ty` covers only `meta/` and `tools/`.
- **No Bazel build is required.** A tool may be a script run directly.
- **Some tools run on the macOS host,** because they need hardware or permissions the devcontainer
  can't reach: Bluetooth, the camera, Apple's SDKs. Everything else runs in the devcontainer like
  any other tooling.
- **No vendor material.** Binaries, firmware and decompiled output stay out of the repository; a
  tool's README says where its inputs come from.

## Tools

| Tool | Runs on | What it does |
|---|---|---|
| [`objc_disassembler/`](objc_disassembler/README.md) | devcontainer | Annotated arm64 disassembly of Objective-C methods in a Mach-O |
| [`intel_hex/`](intel_hex/README.md) | devcontainer | Intel HEX firmware: segments, flat binary, ARM ELF, comparison |
| [`ghidra/`](ghidra/README.md) | devcontainer | Headless Ghidra setup and a decompile-everything script |
| [`apk_strings/`](apk_strings/README.md) | devcontainer | Every string in an Android APK's DEX files |
| [`ble_probe/`](ble_probe/README.md) | macOS host | Bluetooth Low Energy scan, GATT dump, and scripted writes |
| [`light_meter/`](light_meter/README.md) | macOS host | Colour and brightness of a camera region over time |

`macos_app.sh` is shared by the host tools: it compiles Swift sources into the minimal app bundle
macOS requires before it lets a process use Bluetooth or the camera. macOS judges a binary started
from a shell by the app that launched it, and refuses a process whose bundle doesn't declare the
matching usage key, so these tools run from a bundle launched with `open`. Each build is ad-hoc
signed, and macOS may ask for permission again after a rebuild.
