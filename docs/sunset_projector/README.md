# Sunset projector research

How the "Sunset lights" projector (model `LT-06`) and its MeRGBW apps were taken apart, and what
was found that the protocol specification doesn't need. The specification itself —
what the projector accepts and what was verified on it — is
[`libs/sunset_projector/PROTOCOL.md`](../../libs/sunset_projector/PROTOCOL.md). These notes are
the evidence behind it and the starting points for what is still unknown.

- [`ios_app.md`](ios_app.md): MeRGBW for iOS, its command builders and model gating.
- [`android_app.md`](android_app.md): MeRGBW for Android, device types and the update path.
- [`firmware.md`](firmware.md): the firmware the apps bundle, and how to read the projector's own.
- [`hardware.md`](hardware.md): teardown, chip candidates and pin-outs.
- [`scripts/light_meter/`](scripts/light_meter/README.md): measures light through the Mac's
  camera, used to verify the projector's behaviour from recordings.

## Materials not in this repository

This repository is public, and the apps and firmware are the vendor's copyrighted work, so none of
them is committed. Each is identified by its SHA-256, so a copy obtained again can be checked
against what these notes describe.

- **MeRGBW for iOS 1.2.3** (build 20261109.2, bundle ID `com.mergbw.support.electronicelite`),
  from the Mac App Store. Its executable `LEDProject`:
  `672e5441bc4ba8ad02dc5d1dde8e60130d7393024628d0e377d3dbecdb3571c5`.
- **`simpleBlePeripheral.hex16`**, bundled in the iOS app:
  `e7f03bbeb613ff7f6d1de1ef362c6fbc3a0689e5838210b81eeb675491b0d6b6`.
- **`MeRGBW.apk`** (23.0 MB, dated 2026-11-12), from the vendor at
  `https://www.szwest.com/mergbw/MeRGBW.APK`:
  `8906d09b9a6ba855fb6498dc01ac534eabcf10ac62d7cecdd6e66ae2a5ea0183`.
- **Firmware bundled in the APK**, under `assets/firmware/`:
  - `simpleBlePeripheral-TG201.hex16`:
    `554eb39d95ed275be6e6d27736333dc0274fdfd3d7b00f42e867e53ad988c6f7`
  - `simpleBlePeripheral-TG609.hex16`:
    `f4ae4d73c8727d5efebb801db1d161ec92c420828f5834e7f538237be588484c`
  - `simpleBlePeripheral-TG610.hex16`:
    `b0c79bcfa008f9d95ca78d21398b7787a1000c2ca29abf4812e794db3ad4ffc1`
- **PHY6230 and PHY6250 datasheets**, from [pvvx/PHY62x2](https://github.com/pvvx/PHY62x2)
  `Doc/`.

The vendor's site serves only the current Android build. Older Android builds are on third-party
mirrors (APKPure lists versions back to 1.1.3, from January 2024); verify a mirror's copy by its
signing certificate against the vendor's APK before trusting it.
