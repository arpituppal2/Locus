#!/usr/bin/env bash
# install_dock_app.sh
# Creates a real macOS .app bundle that wraps run_app.sh
# and optionally adds it to the Dock.
#
# Usage:  bash install_dock_app.sh
# The app is placed in ~/Applications/Locus.app

set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_NAME="Locus"
APP_DIR="$HOME/Applications/$APP_NAME.app"

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
  <key>CFBundleVersion</key>     <string>1.0</string>
  <key>CFBundleExecutable</key>  <string>launcher</string>
  <key>CFBundleIconFile</key>    <string>AppIcon</string>
  <key>LSUIElement</key>         <false/>
  <key>NSHighResolutionCapable</key> <true/>
  <key>NSPrincipalClass</key>    <string>NSApplication</string>
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

# ── add to Dock ────────────────────────────────────────────────────────────
echo "==> Adding to Dock..."
defaults write com.apple.dock persistent-apps -array-add \
  "<dict>\
    <key>tile-data</key>\
    <dict>\
      <key>file-data</key>\
      <dict>\
        <key>_CFURLString</key><string>$APP_DIR</string>\
        <key>_CFURLStringType</key><integer>0</integer>\
      </dict>\
    </dict>\
  </dict>"

killall Dock

echo ""
echo "✓  Locus.app installed to ~/Applications/"
echo "✓  Added to your Dock (Dock will restart briefly)"
echo ""
echo "Double-click the Dock icon — or run:  open \"$APP_DIR\""
