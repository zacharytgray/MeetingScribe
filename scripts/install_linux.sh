#!/usr/bin/env bash
# MeetingScribe Linux installer
# Installs system deps (PortAudio, libsndfile, ffmpeg), creates a venv,
# installs Python deps, and writes launcher scripts to ~/.local/bin.
set -euo pipefail

: "${VENV_DIR:=$HOME/.meetingscribe/venv}"
: "${BIN_DIR:=$HOME/.local/bin}"
: "${REPO_DIR:=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

echo "=== MeetingScribe Linux Installer ==="
echo

# ---------------------------------------------------------------------------
# 1. Install system dependencies
# ---------------------------------------------------------------------------
if command -v apt-get &>/dev/null; then
  echo "[+] Installing system packages via apt…"
  sudo apt-get update -qq
  sudo apt-get install -y portaudio19-dev libsndfile1 ffmpeg python3-venv python3-pip
elif command -v dnf &>/dev/null; then
  echo "[+] Installing system packages via dnf…"
  sudo dnf install -y portaudio-devel libsndfile ffmpeg python3-pip
elif command -v pacman &>/dev/null; then
  echo "[+] Installing system packages via pacman…"
  sudo pacman -Sy --noconfirm portaudio libsndfile ffmpeg python-pip
else
  echo "[!] Package manager not detected. Please install: portaudio19-dev libsndfile1 ffmpeg"
fi

# ---------------------------------------------------------------------------
# 2. PulseAudio monitor source info
# ---------------------------------------------------------------------------
echo
echo "[i] MeetingScribe captures system audio via a PulseAudio/PipeWire monitor source."
echo "    To verify your monitor source is available:"
echo "      pactl list sources short | grep monitor"
echo "    The monitor source is usually auto-detected. If not, set 'audio_device_index'"
echo "    in your config after running: meetingscribe devices"
echo

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
  echo "[+] Creating venv at ${VENV_DIR}..."
  python3 -m venv "$VENV_DIR"
else
  echo "[✓] Venv already exists at $VENV_DIR."
fi

# ---------------------------------------------------------------------------
# 4. Install Python dependencies
# ---------------------------------------------------------------------------
echo "[+] Installing Python dependencies (CPU-only PyTorch)…"
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install torch --index-url https://download.pytorch.org/whl/cpu --quiet
"$VENV_DIR/bin/pip" install -e "$REPO_DIR" --quiet

# ---------------------------------------------------------------------------
# 5. Write launcher scripts
# ---------------------------------------------------------------------------
mkdir -p "$BIN_DIR"

cat > "$BIN_DIR/meetingscribe" <<EOF
#!/usr/bin/env bash
VENV_DIR="\$HOME/.meetingscribe/venv"
REPO_DIR="$REPO_DIR"
exec "\$VENV_DIR/bin/python" "\$REPO_DIR/cli.py" "\$@"
EOF
chmod +x "$BIN_DIR/meetingscribe"

cat > "$BIN_DIR/meetingscribe-tray" <<EOF
#!/usr/bin/env bash
VENV_DIR="\$HOME/.meetingscribe/venv"
REPO_DIR="$REPO_DIR"
exec "\$VENV_DIR/bin/python" "\$REPO_DIR/tray.py" "\$@"
EOF
chmod +x "$BIN_DIR/meetingscribe-tray"

echo "[✓] Launcher scripts written to $BIN_DIR"

# ---------------------------------------------------------------------------
# 6. Ensure BIN_DIR is on PATH
# ---------------------------------------------------------------------------
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
  SHELL_NAME="$(basename "${SHELL:-}")"
  case "$SHELL_NAME" in
    bash) RC_FILE="$HOME/.bashrc" ;;
    zsh) RC_FILE="$HOME/.zshrc" ;;
    *) RC_FILE="$HOME/.profile" ;;
  esac

  PATH_LINE="export PATH=\"$BIN_DIR:\$PATH\""
  touch "$RC_FILE"

  if ! grep -Fqx "$PATH_LINE" "$RC_FILE"; then
    echo >> "$RC_FILE"
    echo "# Added by MeetingScribe installer" >> "$RC_FILE"
    echo "$PATH_LINE" >> "$RC_FILE"
    echo "[+] Added $BIN_DIR to PATH in $RC_FILE"
  else
    echo "[i] PATH entry for $BIN_DIR already exists in $RC_FILE"
  fi

  echo "[i] Open a new terminal, or run:"
  echo "    export PATH=\"$BIN_DIR:\$PATH\""
fi

# ---------------------------------------------------------------------------
# 7. GNOME tray note
# ---------------------------------------------------------------------------
echo
echo "[i] On GNOME, the tray icon may require the AppIndicator extension:"
echo "    https://extensions.gnome.org/extension/615/appindicator-support/"

echo
echo "=== Installation complete ==="
echo "Run 'meetingscribe setup' to configure API keys and audio device."
