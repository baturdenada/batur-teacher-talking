#!/bin/bash
# Batur Teacher Talking — one-command installer for macOS
#   curl -fsSL https://raw.githubusercontent.com/baturdenada/batur-teacher-talking/main/install.sh | bash
# Optional: preload the Gemini key so the student never sees a prompt:
#   curl -fsSL https://raw.githubusercontent.com/baturdenada/batur-teacher-talking/main/install.sh | BTT_KEY="your-key" bash
set -e
REPO="baturdenada/batur-teacher-talking"
ARCH="$(uname -m)"                                   # arm64 (Apple silicon) or x86_64 (Intel)
[ "$ARCH" = "arm64" ] || ARCH="x86_64"
URL="https://github.com/$REPO/releases/latest/download/BaturTeacherTalking-mac-$ARCH.zip"
DIR="$HOME/Desktop/Batur Teacher Talking"
APP="$DIR/Batur Teacher Talking.app"
TMP="$(mktemp -d)"

echo "Installing Batur Teacher Talking ($ARCH)..."
pkill -f "Batur Teacher Talking.app" 2>/dev/null || true
curl -fL --progress-bar "$URL" -o "$TMP/app.zip"
mkdir -p "$DIR"
rm -rf "$APP"
ditto -x -k "$TMP/app.zip" "$DIR"
xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true
rm -rf "$TMP"

if [ -n "$BTT_KEY" ]; then
  DATA="$HOME/Library/Application Support/BaturTeacherTalking"
  mkdir -p "$DATA"
  printf '%s' "$BTT_KEY" > "$DATA/api_key.txt"
  echo "Gemini key saved."
fi

echo "Done! Opening the app (allow microphone access when macOS asks)."
open "$APP"
