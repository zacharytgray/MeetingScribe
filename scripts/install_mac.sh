#!/usr/bin/env bash
# MeetingScribe macOS installer
# Installs BlackHole (if needed), creates a venv, installs Python deps,
# and writes launcher scripts to ~/.local/bin.
set -euo pipefail

VENV_DIR="$HOME/.meetingscribe/venv"
BIN_DIR="$HOME/.local/bin"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=== MeetingScribe macOS Installer ==="
echo

# ---------------------------------------------------------------------------
# 1. Check for Homebrew
# ---------------------------------------------------------------------------
if ! command -v brew &>/dev/null; then
  echo "[!] Homebrew not found. Install it from https://brew.sh and re-run this script."
  exit 1
fi

# ---------------------------------------------------------------------------
# 2. Audio capture — audiotee (macOS 14.2+) or BlackHole (fallback)
# ---------------------------------------------------------------------------
MACOS_MAJOR=$(sw_vers -productVersion | cut -d. -f1)

if [ "$MACOS_MAJOR" -ge 14 ]; then
  # macOS 14 Sonoma+ — use audiotee (CoreAudio Taps, no virtual driver needed)
  AUDIOTEE_BIN="$BIN_DIR/audiotee"
  if [ -x "$AUDIOTEE_BIN" ] || command -v audiotee &>/dev/null; then
    echo "[✓] audiotee already installed."
  else
    echo "[+] Installing audiotee (driver-free system audio capture for macOS 14.2+)…"
    # Fetch the latest release tag from GitHub
    AUDIOTEE_TAG=$(curl -fsSL https://api.github.com/repos/makeusabrew/audiotee/releases/latest \
      | grep '"tag_name"' | head -1 | cut -d'"' -f4)
    if [ -z "$AUDIOTEE_TAG" ]; then
      echo "[!] Could not fetch audiotee release tag. Falling back to BlackHole."
      MACOS_MAJOR=0  # force BlackHole path below
    else
      mkdir -p "$BIN_DIR"
      curl -fsSL \
        "https://github.com/makeusabrew/audiotee/releases/download/$AUDIOTEE_TAG/audiotee" \
        -o "$AUDIOTEE_BIN"
      chmod +x "$AUDIOTEE_BIN"
      # Remove macOS Gatekeeper quarantine attribute added to downloaded binaries
      xattr -d com.apple.quarantine "$AUDIOTEE_BIN" 2>/dev/null || true
      echo "[✓] audiotee $AUDIOTEE_TAG installed to $AUDIOTEE_BIN"
      echo
      echo "[i] audiotee captures all system audio without BlackHole or any manual setup."
      echo "    Volume control works normally. No Audio MIDI Setup changes needed."
      echo "    On first recording, macOS will ask permission for System Audio Recording."
      echo
    fi
  fi
fi

if [ "$MACOS_MAJOR" -lt 14 ]; then
  # macOS 13 and earlier — use BlackHole virtual audio driver
  if ! brew list blackhole-2ch &>/dev/null; then
    echo "[+] Installing BlackHole 2ch virtual audio driver (macOS < 14 fallback)…"
    brew install blackhole-2ch
  else
    echo "[✓] BlackHole 2ch already installed."
  fi
  echo
  echo "[i] Open 'Audio MIDI Setup' (Applications → Utilities),"
  echo "    create a 'Multi-Output Device' with your speakers + BlackHole 2ch,"
  echo "    then set that device as your system output before recording."
  echo
fi

# ---------------------------------------------------------------------------
# 3. Create Python venv
# ---------------------------------------------------------------------------
if ! command -v python3 &>/dev/null; then
  echo "[!] python3 not found. Install Python 3.10+ and re-run."
  exit 1
fi

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "[+] Using Python $PYTHON_VERSION"

if [ ! -d "$VENV_DIR" ]; then
  echo "[+] Creating venv at $VENV_DIR…"
  python3 -m venv "$VENV_DIR"
else
  echo "[✓] Venv already exists at $VENV_DIR."
fi

# ---------------------------------------------------------------------------
# 4. Install Python dependencies
# ---------------------------------------------------------------------------
echo "[+] Installing Python dependencies…"
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
# On macOS, PyTorch is distributed via PyPI directly (no special index needed).
# Apple Silicon Macs get MPS support; Intel Macs get CPU-only.
"$VENV_DIR/bin/pip" install torch --quiet
"$VENV_DIR/bin/pip" install -e "$REPO_DIR" --quiet

# ---------------------------------------------------------------------------
# 5. Write launcher scripts
# ---------------------------------------------------------------------------
mkdir -p "$BIN_DIR"

cat > "$BIN_DIR/meetingscribe" <<EOF
#!/usr/bin/env bash
exec "$VENV_DIR/bin/python" "$REPO_DIR/cli.py" "\$@"
EOF
chmod +x "$BIN_DIR/meetingscribe"

cat > "$BIN_DIR/meetingscribe-tray" <<EOF
#!/usr/bin/env bash
exec "$VENV_DIR/bin/python" "$REPO_DIR/tray.py" "\$@"
EOF
chmod +x "$BIN_DIR/meetingscribe-tray"

echo "[✓] Launcher scripts written to $BIN_DIR"

# ---------------------------------------------------------------------------
# 6. PATH reminder
# ---------------------------------------------------------------------------
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
  echo
  echo "[i] Add $BIN_DIR to your PATH. Add this to ~/.zshrc or ~/.bash_profile:"
  echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

echo
echo "=== Installation complete ==="
echo "Run 'meetingscribe setup' to configure API keys and audio device."
