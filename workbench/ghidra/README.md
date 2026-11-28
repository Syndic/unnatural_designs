# ghidra

Running [Ghidra](https://ghidra-sre.org) headless in the devcontainer, and `DecompileAll.java`, a
post-script that decompiles every function in a program into one C file.

No automated tests. Last checked by hand on 2026-11-27 with Ghidra 12.2 on linux arm64: it
decompiled all 793 functions of the iOS MeRGBW app's bundled firmware.

## Setting up Ghidra in the devcontainer

Nothing here is installed in the image; these go into the running container and are lost when it
is rebuilt.

- **JDK 25.** Ghidra 12.2 refuses JDK 21. Set `JAVA_HOME` to it.
- **The arm64 decompiler.** Ghidra's release ships no Linux arm64 decompiler, and the devcontainer
  on an Apple-silicon Mac is arm64. Build it in the Ghidra tree with
  `support/gradle/gradlew buildNatives` (it needs `bison` and `flex`), then copy
  `build/os/linux_arm_64` to `os/linux_arm_64`.

## Running

```bash
<ghidra>/support/analyzeHeadless <project-dir> <project-name> \
  -import <program> -processor <language-id> -overwrite \
  -scriptPath workbench/ghidra -postScript DecompileAll.java <out.c>
```

For raw firmware, convert it to an ELF first with [`intel_hex`](../intel_hex/README.md) and name the
language explicitly, e.g. `ARM:LE:32:Cortex` for a Cortex-M part.
