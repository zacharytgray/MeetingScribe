#!/usr/bin/env bash
# build_app.sh — Generate MeetingScribe.app bundle
#
# Creates a thin .app wrapper that launches the Python venv.
# The heavy deps (torch, whisper, etc.) live in ~/.meetingscribe/venv,
# so the .app itself is ~200 KB.
#
# Usage:
#   ./scripts/build_app.sh              # builds to ./build/MeetingScribe.app
#   ./scripts/build_app.sh --install    # also copies to /Applications

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
BUILD_DIR="$ROOT/build"
APP="$BUILD_DIR/MeetingScribe.app"
CONTENTS="$APP/Contents"
MACOS="$CONTENTS/MacOS"
RESOURCES="$CONTENTS/Resources"

VENV_DIR="$HOME/.meetingscribe/venv"
ICON="$ROOT/assets/AppIcon.icns"

# generate icon if missing
if [ ! -f "$ICON" ]; then
    echo "Generating app icon..."
    "$VENV_DIR/bin/python" "$SCRIPT_DIR/generate_icon.py" || {
        echo "Warning: could not generate icon, continuing without it"
        ICON=""
    }
fi

echo "Building MeetingScribe.app..."

rm -rf "$APP"
mkdir -p "$MACOS" "$RESOURCES"

# --- launcher script ---
cat > "$MACOS/MeetingScribe" << 'LAUNCHER'
#!/bin/bash
# MeetingScribe.app launcher — finds the venv and runs the native app module
VENV="$HOME/.meetingscribe/venv"

if [ ! -d "$VENV" ]; then
    osascript -e 'display alert "MeetingScribe" message "Python environment not found.\n\nRun the installer first:\n  cd MeetingScribe && ./scripts/install_mac.sh" as critical'
    exit 1
fi

PYTHON="$VENV/bin/python"
if [ ! -x "$PYTHON" ]; then
    osascript -e 'display alert "MeetingScribe" message "Python not found in venv.\n\nRe-run the installer:\n  cd MeetingScribe && ./scripts/install_mac.sh" as critical'
    exit 1
fi

export KMP_DUPLICATE_LIB_OK=TRUE
export PYTORCH_ENABLE_MPS_FALLBACK=1
export OMP_NUM_THREADS=1

exec "$PYTHON" -m meetingscribe.app
LAUNCHER
chmod +x "$MACOS/MeetingScribe"

# --- icon ---
if [ -n "$ICON" ] && [ -f "$ICON" ]; then
    cp "$ICON" "$RESOURCES/AppIcon.icns"
fi

# --- Info.plist ---
# get version from pyproject.toml
VERSION=$(grep 'version = ' "$ROOT/pyproject.toml" | head -1 | sed 's/.*"\(.*\)".*/\1/')
[ -z "$VERSION" ] && VERSION="1.0.0"

cat > "$CONTENTS/Info.plist" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>MeetingScribe</string>
    <key>CFBundleDisplayName</key>
    <string>MeetingScribe</string>
    <key>CFBundleIdentifier</key>
    <string>com.zacharygray.meetingscribe</string>
    <key>CFBundleVersion</key>
    <string>${VERSION}</string>
    <key>CFBundleShortVersionString</key>
    <string>${VERSION}</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleExecutable</key>
    <string>MeetingScribe</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>LSMinimumSystemVersion</key>
    <string>13.0</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSSupportsAutomaticGraphicsSwitching</key>
    <true/>
    <key>NSMicrophoneUsageDescription</key>
    <string>MeetingScribe needs microphone access to capture your voice during meetings.</string>
    <key>NSAppleEventsUsageDescription</key>
    <string>MeetingScribe needs to open files and folders.</string>
</dict>
</plist>
PLIST

# exclude build/ copy from Spotlight indexing
touch "$BUILD_DIR/.metadata_never_index"

echo "Built: $APP"
echo "Size: $(du -sh "$APP" | cut -f1)"

# --- optional install ---
if [ "${1:-}" = "--install" ]; then
    DEST="/Applications/MeetingScribe.app"
    echo ""
    if [ -d "$DEST" ]; then
        echo "Replacing existing $DEST..."
        rm -rf "$DEST"
    fi
    cp -R "$APP" "$DEST"
    echo "Installed to $DEST"

    # remove build/ copy so Spotlight doesn't index it
    rm -rf "$APP"

    # register /Applications copy with Launch Services (Spotlight)
    /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$DEST" 2>/dev/null || true
    echo "Registered with Launch Services (Spotlight)"
    echo ""
    echo "You can now launch MeetingScribe from Spotlight or /Applications."
fi
