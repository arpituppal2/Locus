#!/usr/bin/env bash
# install_dock_app.sh
# Creates a real macOS menu-bar .app bundle that wraps run_app.sh.
#
# Usage:  bash install_dock_app.sh
# The app is placed in ~/Applications/Locus.app

set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_NAME="Locus"
APP_DIR="$HOME/Applications/$APP_NAME.app"
LAUNCH_AGENT="$HOME/Library/LaunchAgents/com.locus.local.app.plist"

echo "==> Building $APP_DIR"

# ── directories ────────────────────────────────────────────────────────────
mkdir -p "$APP_DIR/Contents/MacOS"
mkdir -p "$APP_DIR/Contents/Resources"

# ── Info.plist ─────────────────────────────────────────────────────────────
cat > "$APP_DIR/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key>  <string>com.locus.local.app</string>
  <key>CFBundleName</key>        <string>Locus</string>
  <key>CFBundleDisplayName</key> <string>Locus</string>
  <key>CFBundlePackageType</key> <string>APPL</string>
  <key>CFBundleVersion</key>     <string>1.0</string>
  <key>CFBundleExecutable</key>  <string>launcher</string>
  <key>CFBundleIconFile</key>    <string>AppIcon</string>
  <key>LSUIElement</key>         <true/>
  <key>NSHighResolutionCapable</key> <true/>
  <key>NSPrincipalClass</key>    <string>NSApplication</string>
  <key>NSMicrophoneUsageDescription</key>
  <string>Locus uses the microphone only when you start Voice Mode.</string>
  <key>NSSpeechRecognitionUsageDescription</key>
  <string>Locus can transcribe your voice locally through macOS speech services when available.</string>
  <key>NSAppleEventsUsageDescription</key>
  <string>Locus uses automation only for local app-control tools you approve.</string>
</dict>
</plist>
PLIST

# ── launcher script ────────────────────────────────────────────────────────
cat > "$APP_DIR/Contents/MacOS/launcher" <<LAUNCHER
#!/usr/bin/env bash
exec "$DIR/run_app.sh"
LAUNCHER
chmod +x "$APP_DIR/Contents/MacOS/launcher"

# ── icon ──────────────────────────────────────────────────────────────────
ICON_PNG="$DIR/assets/icons/locus-app-icon-1024.png"
ICON_ICNS="$DIR/assets/icons/macos/Locus.icns"
if [ ! -f "$ICON_PNG" ] || [ ! -f "$ICON_ICNS" ]; then
  echo "==> Generating Locus icon assets..."
  "$DIR/.venv/bin/python" "$DIR/scripts/generate_app_icons.py" 2>/dev/null || python3 "$DIR/scripts/generate_app_icons.py"
fi
cp "$ICON_PNG" "$APP_DIR/Contents/Resources/AppIcon.png"
cp "$ICON_ICNS" "$APP_DIR/Contents/Resources/AppIcon.icns"

echo "==> App built: $APP_DIR"

# ── launch at login so Locus lives in the menu bar ─────────────────────────
mkdir -p "$(dirname "$LAUNCH_AGENT")"
cat > "$LAUNCH_AGENT" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.locus.local.app</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/open</string>
    <string>-a</string>
    <string>$APP_DIR</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><false/>
</dict>
</plist>
PLIST

launchctl unload "$LAUNCH_AGENT" >/dev/null 2>&1 || true
launchctl load "$LAUNCH_AGENT" >/dev/null 2>&1 || true

echo ""
echo "✓  Locus.app installed to ~/Applications/"
echo "✓  Locus now runs as a menu-bar app and starts at login"
echo ""
echo "Open it now with:  open \"$APP_DIR\""
