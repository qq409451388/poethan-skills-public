#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONFIGURATION="${1:-debug}"
BUILD_DIR="$ROOT_DIR/.build"
APP_DIR="$BUILD_DIR/Poethan Sentinel.app"

cd "$ROOT_DIR"
swift build -c "$CONFIGURATION"
BIN_DIR="$(swift build -c "$CONFIGURATION" --show-bin-path)"

mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"
cp "$BIN_DIR/PoethanSentinel" "$APP_DIR/Contents/MacOS/PoethanSentinel"
cp "$ROOT_DIR/Resources/Info.plist" "$APP_DIR/Contents/Info.plist"
rm -rf "$APP_DIR/PoethanSentinel_PoethanSentinel.bundle"
rm -rf "$APP_DIR/Contents/Resources/doris-diagnostic" "$APP_DIR/Contents/Resources/host-performance" "$APP_DIR/Contents/Resources/network-diagnostic"
cp "$ROOT_DIR/Sources/PoethanSentinel/Resources/report-template.html" "$APP_DIR/Contents/Resources/report-template.html"
cp "$ROOT_DIR/Sources/PoethanSentinel/Resources/report-schema.json" "$APP_DIR/Contents/Resources/report-schema.json"
chmod +x "$APP_DIR/Contents/MacOS/PoethanSentinel"

codesign --force --deep --sign - "$APP_DIR"
echo "$APP_DIR"
