#!/usr/bin/env bash
# Build LightMeter.app into <outdir>: capture.swift plus the Info.plist macOS needs before it lets
# the process use the camera. Runs on the macOS host, not in the devcontainer.
# Usage: build.sh <outdir>
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <outdir>" >&2
  exit 64
fi
here="$(cd "$(dirname "$0")" && pwd)"
app="$1/LightMeter.app"
mkdir -p "$app/Contents/MacOS"
cat >"$app/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>local.unnatural-designs.light-meter</string>
  <key>CFBundleName</key><string>LightMeter</string>
  <key>CFBundleExecutable</key><string>LightMeter</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSUIElement</key><true/>
  <key>NSCameraUsageDescription</key><string>Measure light in the camera's view.</string>
</dict></plist>
EOF
swiftc -O "$here/capture.swift" -o "$app/Contents/MacOS/LightMeter"
codesign --force -s - "$app"
echo "$app"
