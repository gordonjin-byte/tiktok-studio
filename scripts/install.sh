#!/bin/bash
# TikTok Studio installer: data dirs, venv, whisper model, seed assets, launchd.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="$HOME/TikTokStudio"
MODEL="$DATA_DIR/models/ggml-small.en.bin"
PLIST_SRC="$APP_DIR/launchd/com.gordonjin.tiktokstudio.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.gordonjin.tiktokstudio.plist"
LABEL="com.gordonjin.tiktokstudio"

echo "→ data directories"
mkdir -p "$DATA_DIR"/{inbox,originals,artifacts,renders,music,sfx,models,tmp,logs}

echo "→ python venv"
if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
  python3 -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

echo "→ checking binaries"
for bin in /opt/homebrew/opt/ffmpeg-full/bin/ffmpeg /opt/homebrew/bin/whisper-cli; do
  [ -x "$bin" ] || { echo "MISSING: $bin (brew install ffmpeg-full whisper-cpp)"; exit 1; }
done
[ -x "$HOME/.local/bin/claude" ] || echo "WARN: claude CLI not found — AI edit decisions will use heuristic fallback"

echo "→ node / remotion (script-driven overlays)"
command -v node >/dev/null 2>&1 || { echo "MISSING: node >=20 (brew install node@20)"; exit 1; }
NODE_MAJOR=$(node -e 'console.log(process.versions.node.split(".")[0])')
[ "$NODE_MAJOR" -ge 20 ] || { echo "MISSING: node >=20 (found $(node -v))"; exit 1; }
(cd "$APP_DIR/remotion" && npm ci --no-fund --no-audit)
(cd "$APP_DIR/remotion" && npx remotion browser ensure)
(cd "$APP_DIR/remotion" && npm run catalog:build)

echo "→ whisper model"
if [ ! -f "$MODEL" ]; then
  echo "  downloading ggml-small.en.bin (488MB)…"
  curl -L -o "$MODEL" https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin
fi

echo "→ seed sfx/music (skips existing)"
if [ ! -f "$DATA_DIR/sfx/whoosh.wav" ] || [ ! -f "$DATA_DIR/music/lofi-warm.wav" ]; then
  "$APP_DIR/.venv/bin/python" "$APP_DIR/scripts/seed_assets.py" "$DATA_DIR/sfx" "$DATA_DIR/music"
fi

echo "→ launchd agent"
mkdir -p "$HOME/Library/LaunchAgents"
cp "$PLIST_SRC" "$PLIST_DST"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
launchctl kickstart -k "gui/$(id -u)/$LABEL"

sleep 3
if curl -sf http://127.0.0.1:8765/api/stats > /dev/null; then
  echo "✓ TikTok Studio running at http://127.0.0.1:8765"
else
  echo "✗ server not responding — check $DATA_DIR/logs/launchd.err"
  exit 1
fi
