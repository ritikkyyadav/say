#!/bin/bash
# setup.sh - Say install and optional packaging helper.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="${SAY_VENV:-$HOME/.venvs/say}"
APP_NAME="Say"
PYTHON_BIN="${SAY_PYTHON:-}"

if [[ -z "$PYTHON_BIN" ]]; then
  if command -v python3.12 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3.12)"
  elif command -v python3.11 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3.11)"
  else
    PYTHON_BIN="$(command -v python3)"
  fi
fi

echo ""
echo "Say setup"
echo "=================="
echo ""

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required for portaudio and ffmpeg."
  echo "Install it from https://brew.sh, then rerun this script."
  exit 1
fi

echo "Checking Homebrew dependencies..."
brew list portaudio >/dev/null 2>&1 || brew install portaudio
brew list ffmpeg >/dev/null 2>&1 || brew install ffmpeg

echo ""
echo "Creating Python environment: $VENV_DIR"
"$PYTHON_BIN" -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip
python -m pip install -r "$PROJECT_DIR/requirements.txt"

echo ""
echo "Pre-downloading and warming the configured Whisper model..."
cd "$PROJECT_DIR"
python - <<'PY'
import config
from transcriber import Transcriber

print(f"Model: {config.MODEL}")
Transcriber().load()
print("Model cached and ready.")
PY

echo ""
read -r -p "Build Say.app via Makefile? [y/N] " PACKAGE
if [[ "${PACKAGE:-N}" =~ ^[Yy]$ ]]; then
  cd "$PROJECT_DIR"
  make all
  echo "Built: $PROJECT_DIR/dist/$APP_NAME.app"
  echo "DMG:   $PROJECT_DIR/dist/${APP_NAME}-v0.1.0-unsigned.dmg"
  echo ""
  echo "Note: this build is unsigned. To open the .app the first time,"
  echo "right-click it in Finder and choose Open. To codesign + notarize"
  echo "later, run 'make sign notarize' with the appropriate env vars."
fi

echo ""
echo "Setup complete."
echo "Run menu bar mode:"
echo "  source \"$VENV_DIR/bin/activate\""
echo "  python \"$PROJECT_DIR/app.py\""
echo ""
echo "Grant permissions in System Settings > Privacy & Security:"
echo "  - Microphone"
echo "  - Accessibility"
echo "  - Input Monitoring"
echo ""
echo "If you build the .app, grant those permissions to Say.app separately."
