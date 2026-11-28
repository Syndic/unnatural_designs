# apk_strings

`dexstrings.py` prints every string in the string table of each `classes*.dex` file in an Android
APK, prefixed with the DEX file's name — class names, method names, literals, asset paths. It is a
fast first look before decompiling, and needs only the standard library.

```bash
python3 -I workbench/apk_strings/dexstrings.py APP.apk | grep -i firmware
```

For readable source, decompile with [jadx](https://github.com/skylot/jadx) instead.

No automated tests. Last checked by hand on 2026-11-27: it listed 71,378 strings from the MeRGBW
Android APK, including its bundled firmware paths.
