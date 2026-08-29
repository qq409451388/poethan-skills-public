#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PACKAGE_ROOT="$(cd "$ROOT/.." && pwd)"
VERSION="$(node -p "require('$ROOT/frontend/package.json').version")"
OUTPUT="$ROOT/.release"
ARCHIVE="$OUTPUT/poethan-sentinel-webapp-$VERSION.tar.gz"
mkdir -p "$OUTPUT"
"$ROOT/scripts/check.sh"
tar -czf "$ARCHIVE" \
  --exclude='.venv' --exclude='node_modules' --exclude='.npm-cache' --exclude='.pytest_cache' --exclude='__pycache__' --exclude='*.pyc' --exclude='*.tsbuildinfo' --exclude='dist' --exclude='.build' --exclude='.release' --exclude='.DS_Store' --exclude='vite.config.js' --exclude='vite.config.d.ts' \
  -C "$PACKAGE_ROOT/.." "$(basename "$PACKAGE_ROOT")"
shasum -a 256 "$ARCHIVE" > "$ARCHIVE.sha256"
echo "发布包：$ARCHIVE"
cat "$ARCHIVE.sha256"
