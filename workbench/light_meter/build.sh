#!/usr/bin/env bash
# Build LightMeter.app into <outdir>. Runs on the macOS host.
# Usage: build.sh <outdir>
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <outdir>" >&2
  exit 64
fi
here="$(cd "$(dirname "$0")" && pwd)"
"$here/../macos_app.sh" "$1" LightMeter NSCameraUsageDescription \
  "Measure light in the camera's view." "$here/capture.swift"
