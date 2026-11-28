# MeRGBW for iOS

Version 1.2.3, analysed statically. Hashes and source are in the [README](README.md).

## Getting the binary

- On an Apple-silicon Mac, the iPad build installs from the Mac App Store. It lands at
  `/Applications/MeRGBW.app`, a wrapper around `Wrapper/LEDProject.app`, which holds the
  executable `LEDProject` and the bundled firmware `simpleBlePeripheral.hex16`.
- App Store binaries are FairPlay-encrypted, but this one's encrypted range (`cryptsize` in
  `LC_ENCRYPTION_INFO_64`) is only 4096 bytes, so almost all of the code is readable as installed.
- Older iOS builds can in principle be fetched with [`ipatool`](https://github.com/majd/ipatool)
  (`list-versions` then `download --external-version-id`), signed in with an Apple ID. That has not
  been run.

## Analysis method

A Python script using [LIEF](https://lief.re) to parse the Mach-O and
[Capstone](https://www.capstone-engine.org) to disassemble arm64, annotating each method with the
selectors and strings it references. What it has to handle:

- **Chained fixups.** Pointers in the data segments are stored as chained-fixup entries. A rebase
  entry's target is `raw & 0xFFFFFFFFF`, plus the image base when it is below it; an entry with
  bit 63 set is a bind and resolves through the imports instead.
- **Relative method lists.** Method lists use 32-bit offsets relative to each field rather than
  absolute pointers.
- **Selector stubs.** Calls go through `__objc_stubs`, small thunks that load a selector reference
  and branch to `objc_msgSend`; resolving each stub's selector gives readable call sites.
- **Categories.** Some methods are added by categories, listed in `__objc_catlist`, not in the
  class's own method list.

## Where the protocol lives

- `LEDCommandAnalysislogic` builds every frame; `LEDBLEManager` and `LEDDevice` send them and
  decide which builder to use per model.
- The builder each command uses:

  | Command | Builder | Notes |
  |---|---|---|
  | `00` | `syncDeviceStateWithDevice:` | |
  | `01` | `device:open:` | |
  | `02` | `otaVersionWithDevice:` | |
  | `03` | `configWholeLightcolor:` | R G B; the projector's variant |
  | `03` | `configWholecolor:` | hue u16, saturation u16 (0–1000); other models |
  | `05` | `configHHLightness:` | 1 byte; the projector's variant |
  | `05` | `configLightness:` | u16; other models |
  | `06` | `configLightsystemMode:` | 1-byte scene ID |
  | `06` | `configsystemMode:` | `00` + mode; models 1 and 4 only |
  | `07` | `configMusicMode:` | |
  | `08` | `configSensitivity:` | |
  | `0a` | `configTimerautoON…` | |
  | `0b` | `configLightCount:` | |
  | `0c` | `syTimeWithDevice:` | weekday in NSCalendar numbering (1 = Sunday) |
  | `0e` | `bondDeviceCheck` | 6 bytes from the phone's `identifierForVendor` |
  | `0f` | `configsystemModeSpeed:` | |
  | `10` | `configLaserBrightness:`, `device:whiteBulbOpen:` | |
  | `11` | `configMotorSpeed:`, `configBulbWhiteLightness:` | |
  | `04` | `configWcolors:` | multi-colour list, sliced payload |

- The hard-coded handshake frame `55 0e ff 05 98` confirmed the length and checksum rules.

## Model-specific behaviour

- **Scenes.** `LEDTheLightSceneView` is the projector's scene page. It flags 137, 143 and 144 with
  `speed = 1`, which `-[LEDDevice configSystemMode:speed:]` routes through a different command;
  on the device, plain `06` plays all three anyway.
- **Timers.** `-[LEDTimedView creatUI]` labels day buttons with tags 1100 + i for Monday … Sunday
  (i = 0…6), and `senderTouched:` stores `weeks[tag − 1100]`, which is where the Monday-first mask
  came from before the device confirmed it.
- **Music.** `LEDSunsetLightsMusicView` records the microphone through `AudioRecordTask` and streams
  `03` colour frames; nothing about music is sent as a mode. Its styles: Symphony, Red, Blue, Pink,
  Green, Orange, Yellow, Purple, light blue.
- **White light.** Every white path — `-[LEDDevice configWhiteLightness:]` (`11`),
  `configBulbWarmColor:` (`06`), `syncDeviceOpenBulbWhite:` (`10`) — is gated on
  `modelNumber == 6`, a bulb, so the app never drives white on the projector.

## Firmware update path

- `-[LEDDevice checkOTAVersion]` loads the bundled `simpleBlePeripheral.hex16` and pushes it with
  the Phyplus OTA SDK (`PHYBLEManager`, `PPlusOTA`, `PHYBLEModel`).
- The only entry point, `-[LEDSettingViewController checkOTAVersion]`, shows untranslated dialogs
  ("发现新固件版本%@", new firmware version found; "固件升级", firmware upgrade; "立即升级",
  upgrade now). Nothing in 1.2.3 calls it, so the path is unreachable in this version.
- There is no firmware download URL. The vendor hosts the app contacts are `brand.mergbw.com`
  (port 8080 as well) and a usage-tracking endpoint at `/track/appTrack`; the `/api/v1/app_sdk/*`
  paths belong to an advertising SDK.
