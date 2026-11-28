#!/usr/bin/env bash
# Build BLEScan.app, BLEGatt.app and BLEPoke.app into <outdir>. Runs on the macOS host.
# Usage: build.sh <outdir>
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <outdir>" >&2
  exit 64
fi
here="$(cd "$(dirname "$0")" && pwd)"
usage="Probe Bluetooth Low Energy devices."
for pair in BLEScan:scan BLEGatt:gatt BLEPoke:poke; do
  "$here/../macos_app.sh" "$1" "${pair%%:*}" NSBluetoothAlwaysUsageDescription "$usage" \
    "$here/${pair#*:}.swift"
done
