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

# ── icon (generate a simple PNG via Python then convert to icns) ───────────
python3 - <<PYICON
try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "Pillow"])
    from PIL import Image, ImageDraw, ImageFont

import os

size = 512
img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

# Dark rounded square background
pad = 40
draw.rounded_rectangle([pad, pad, size-pad, size-pad],
                       radius=90, fill=(22, 21, 19, 255))

# Teal nested locus mark
inner = 132
draw.rounded_rectangle([inner, inner, size-inner, size-inner],
                       radius=46, fill=(25, 96, 102, 255))
draw.rounded_rectangle([inner+58, inner+58, size-inner-58, size-inner-58],
                       radius=30, fill=(139, 198, 189, 255))

# Letter L in white
try:
    font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 170)
except Exception:
    font = ImageFont.load_default()

draw.text((size//2, size//2), "L", fill=(255,255,255,245),
          font=font, anchor="mm")

out = os.path.expanduser("~/Applications/Locus.app/Contents/Resources/AppIcon.png")
img.save(out)
print(f"Icon saved: {out}")
PYICON

# Convert PNG → icns using sips + iconutil
ICONSET="/tmp/AppIcon.iconset"
mkdir -p "$ICONSET"
for sz in 16 32 64 128 256 512; do
  sips -z $sz $sz \
    "$APP_DIR/Contents/Resources/AppIcon.png" \
    --out "$ICONSET/icon_${sz}x${sz}.png" >/dev/null 2>&1
done
iconutil -c icns "$ICONSET" \
  -o "$APP_DIR/Contents/Resources/AppIcon.icns" 2>/dev/null || true
rm -rf "$ICONSET"

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
