#!/bin/bash
# Builds "~/Applications/Garmin Insights.app" — a tiny launcher that runs this project with uv.
set -euo pipefail
PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
APP="$HOME/Applications/Garmin Insights.app"

mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cat > "$APP/Contents/MacOS/garmin-insights" <<EOF
#!/bin/bash
export PATH="/opt/homebrew/bin:/usr/local/bin:\$HOME/.local/bin:\$PATH"
cd "$PROJECT"
exec "$UV" run --extra forecast garmin-app run >> "\$HOME/.garmin_insights/logs/launcher.log" 2>&1
EOF
chmod +x "$APP/Contents/MacOS/garmin-insights"

cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Garmin Insights</string>
  <key>CFBundleDisplayName</key><string>Garmin Insights</string>
  <key>CFBundleIdentifier</key><string>local.garmin-insights</string>
  <key>CFBundleVersion</key><string>0.1.0</string>
  <key>CFBundleExecutable</key><string>garmin-insights</string>
  <key>CFBundleIconFile</key><string>icon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
EOF

# Icon: render a simple heart-rate glyph to PNG and convert to .icns (best effort).
ICONSET="$(mktemp -d)/icon.iconset"; mkdir -p "$ICONSET"
python3 - "$ICONSET" <<'PY' || true
# Pure-Python PNG: blue rounded square with a white heartbeat line (no extra dependencies).
import math, struct, sys, zlib
N = 512
pts = [(0.12, 0.55), (0.36, 0.55), (0.45, 0.28), (0.55, 0.78), (0.63, 0.45), (0.70, 0.55), (0.88, 0.55)]
pts = [(x * N, y * N) for x, y in pts]
W, R = 0.045 * N, 0.22 * N
def seg_d(px, py, a, b):
    (x1, y1), (x2, y2) = a, b
    dx, dy = x2 - x1, y2 - y1
    t = max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))
rows = []
for y in range(N):
    row = bytearray([0])
    for x in range(N):
        cx, cy = min(max(x, R), N - R), min(max(y, R), N - R)
        inside = math.hypot(x - cx, y - cy) <= R
        if not inside:
            row += bytes([0, 0, 0, 0]); continue
        d = min(seg_d(x, y, pts[i], pts[i + 1]) for i in range(len(pts) - 1))
        row += bytes([255, 255, 255, 255]) if d <= W / 2 else bytes([42, 120, 214, 255])
    rows.append(bytes(row))
def chunk(t, d): return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", N, N, 8, 6, 0, 0, 0)) + \
      chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b"")
open(f"{sys.argv[1]}/icon_512x512@2x.png", "wb").write(png)
PY
if [ -f "$ICONSET/icon_512x512@2x.png" ]; then
  for s in 16 32 128 256 512; do
    sips -z $s $s "$ICONSET/icon_512x512@2x.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
    sips -z $((s*2)) $((s*2)) "$ICONSET/icon_512x512@2x.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/icon.icns" || true
fi
touch "$APP"
echo "Created: $APP"
