#!/usr/bin/env bash
# Compile Swift sources into a minimal, ad-hoc-signed macOS app bundle that declares one privacy
# usage key. macOS lets a process use the camera, Bluetooth and the like only if its bundle declares
# the matching key, and it judges a binary started from a shell by the app that launched it; see
# README.md. Runs on the macOS host.
# Usage: macos_app.sh <outdir> <AppName> <usage-key> <usage-text> <source.swift>...
set -euo pipefail

if [[ $# -lt 5 ]]; then
  echo "usage: $0 <outdir> <AppName> <usage-key> <usage-text> <source.swift>..." >&2
  exit 64
fi
outdir="$1" name="$2" key="$3" text="$4"
shift 4
app="$outdir/$name.app"
id="local.unnatural-designs.workbench.$(tr '[:upper:]' '[:lower:]' <<<"$name")"
mkdir -p "$app/Contents/MacOS"
cat >"$app/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>$id</string>
  <key>CFBundleName</key><string>$name</string>
  <key>CFBundleExecutable</key><string>$name</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSUIElement</key><true/>
  <key>$key</key><string>$text</string>
</dict></plist>
EOF
swiftc -O "$@" -o "$app/Contents/MacOS/$name"
codesign --force -s - "$app"
echo "$app"
