#!/bin/bash
# build a signed, notarized DMG for distribution
set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="MeetingScribe"
BUILD_DIR="$PROJECT_DIR/build"
DIST_DIR="$PROJECT_DIR/dist"
APP_PATH="$BUILD_DIR/Build/Products/Release/$APP_NAME.app"

# read version from project.yml
VERSION=$(grep 'MARKETING_VERSION' "$PROJECT_DIR/project.yml" | head -1 | sed 's/.*"\(.*\)".*/\1/')
DMG_NAME="$APP_NAME-$VERSION.dmg"
DMG_PATH="$DIST_DIR/$DMG_NAME"

# require DEVELOPMENT_TEAM
if [ -z "$DEVELOPMENT_TEAM" ]; then
    echo "ERROR: DEVELOPMENT_TEAM not set."
    echo "  export DEVELOPMENT_TEAM=YOUR_TEAM_ID"
    exit 1
fi

echo "=== Building $APP_NAME v$VERSION (Release) ==="
mkdir -p "$DIST_DIR"

# unlock keychain
security unlock-keychain ~/Library/Keychains/login.keychain-db 2>/dev/null || true

# build release
xcodebuild \
    -project "$PROJECT_DIR/MeetingScribe.xcodeproj" \
    -scheme MeetingScribe \
    -configuration Release \
    -derivedDataPath "$BUILD_DIR" \
    DEVELOPMENT_TEAM="$DEVELOPMENT_TEAM" \
    CODE_SIGN_IDENTITY="Developer ID Application" \
    CODE_SIGN_STYLE=Manual \
    ENABLE_HARDENED_RUNTIME=YES \
    OTHER_CODE_SIGN_FLAGS="--timestamp" \
    CODE_SIGN_ENTITLEMENTS="$PROJECT_DIR/MeetingScribe/MeetingScribe-Release.entitlements" \
    build 2>&1 | grep -E '(error:|warning:|BUILD)' || true

if [ ! -d "$APP_PATH" ]; then
    echo "ERROR: Build failed — $APP_PATH not found"
    exit 1
fi

# re-sign all nested binaries (Sparkle helpers) with Developer ID + timestamp
echo "=== Re-signing nested frameworks ==="
SIGN_ID="Developer ID Application: Zachary Gray (WYY8494SWG)"
find "$APP_PATH/Contents/Frameworks" -type f -perm +111 -o -name "*.dylib" | while read bin; do
    codesign --force --sign "$SIGN_ID" --timestamp --options runtime "$bin" 2>/dev/null || true
done
find "$APP_PATH/Contents/Frameworks" -name "*.xpc" -o -name "*.app" | while read bundle; do
    codesign --force --deep --sign "$SIGN_ID" --timestamp --options runtime "$bundle" 2>/dev/null || true
done
find "$APP_PATH/Contents/Frameworks" -name "*.framework" | while read fw; do
    codesign --force --sign "$SIGN_ID" --timestamp --options runtime "$fw" 2>/dev/null || true
done
codesign --force --sign "$SIGN_ID" --timestamp --options runtime \
    --entitlements "$PROJECT_DIR/MeetingScribe/MeetingScribe-Release.entitlements" \
    "$APP_PATH"

# re-sign audiotee so TCC grants survive recompilation
AUDIOTEE_PATH="$(which audiotee 2>/dev/null || echo /usr/local/bin/audiotee)"
if [ -f "$AUDIOTEE_PATH" ]; then
    echo "=== Re-signing audiotee ==="
    codesign --force --sign "$SIGN_ID" --identifier "com.meetingscribe.audiotee" \
        --options runtime --timestamp "$AUDIOTEE_PATH"
fi

echo "=== Creating DMG ==="

rm -f "$DMG_PATH"

STAGING="$BUILD_DIR/dmg-staging"
rm -rf "$STAGING"
mkdir -p "$STAGING"
cp -R "$APP_PATH" "$STAGING/"
ln -s /Applications "$STAGING/Applications"

hdiutil create -volname "$APP_NAME" \
    -srcfolder "$STAGING" \
    -ov -format UDZO \
    "$DMG_PATH"

rm -rf "$STAGING"

echo "=== DMG created: $DMG_PATH ==="

# notarize
echo "=== Notarizing ==="
echo "(requires stored credentials — run 'xcrun notarytool store-credentials MeetingScribe' first)"

if xcrun notarytool submit "$DMG_PATH" \
    --keychain-profile "MeetingScribe" \
    --wait 2>&1; then
    echo "=== Stapling ==="
    xcrun stapler staple "$DMG_PATH"
    echo "=== Done: $DMG_PATH (signed + notarized) ==="
else
    echo ""
    echo "Notarization failed or credentials not found."
    echo "The unsigned DMG is still at: $DMG_PATH"
    echo ""
    echo "To set up notarization credentials (one-time):"
    echo "  xcrun notarytool store-credentials MeetingScribe \\"
    echo "    --apple-id YOUR_APPLE_ID \\"
    echo "    --team-id $DEVELOPMENT_TEAM \\"
    echo "    --password APP_SPECIFIC_PASSWORD"
    echo ""
    echo "Generate an app-specific password at: https://appleid.apple.com/account/manage"
fi
