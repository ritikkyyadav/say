#!/bin/bash
# launch.sh — Auto-Start Script for Say
# ============================================================
# Say · On-device voice dictation for macOS
#
# Add this script to:
#   System Settings → General → Login Items → Add → select launch.sh
#
# For a cleaner setup, use a launchd plist or package as a .app.
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PATH="$HOME/.venvs/say"

# Activate virtual environment
if [ -f "$VENV_PATH/bin/activate" ]; then
    source "$VENV_PATH/bin/activate"
else
    echo "[✗] Virtual environment not found at $VENV_PATH"
    echo "    Create it with: python3 -m venv $VENV_PATH"
    exit 1
fi

# Run the menu bar app
cd "$SCRIPT_DIR"
python3 app.py
