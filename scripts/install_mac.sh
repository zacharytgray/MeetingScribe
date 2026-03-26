#!/usr/bin/env bash
# MeetingScribe macOS installer
# Installs BlackHole (if needed), creates a venv, installs Python deps,
# and writes launcher scripts to ~/.local/bin.
set -euo pipefail

: "${VENV_DIR:=$HOME/.meetingscribe/venv}"
: "${BIN_DIR:=$HOME/.local/bin}"
: "${REPO_DIR:=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

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
  AUDIOTEE_BIN="/usr/local/bin/audiotee"
  if command -v audiotee &>/dev/null; then
    echo "[✓] audiotee already installed."
    # macOS 16 (Tahoe) requires the binary to be code-signed so macOS can
    # anchor a TCC privacy entry to it. Without this, audiotee may run without
    # errors but receive only silence. Re-sign on every install to cover
    # manually-installed binaries and macOS upgrades.
    AUDIOTEE_PATH="$(command -v audiotee)"
    if codesign --sign - --force "$AUDIOTEE_PATH" 2>/dev/null; then
      echo "[✓] audiotee signed (ad-hoc) for macOS privacy permissions."
    elif sudo codesign --sign - --force "$AUDIOTEE_PATH" 2>/dev/null; then
      echo "[✓] audiotee signed (ad-hoc, via sudo) for macOS privacy permissions."
    else
      echo "[!] codesign failed. Run manually: sudo codesign --sign - --force \$(which audiotee)"
    fi
  elif ! command -v swift &>/dev/null; then
    echo "[!] Swift not found. Install Xcode Command Line Tools then re-run:"
    echo "    xcode-select --install"
    echo "    Then re-run this script to build audiotee."
    MACOS_MAJOR=0  # fall through to BlackHole
  else
    echo "[+] Building audiotee from source (driver-free audio capture for macOS 14.2+)…"
    AUDIOTEE_TMP=$(mktemp -d)
    git clone --depth 1 https://github.com/makeusabrew/audiotee.git "$AUDIOTEE_TMP/audiotee" 2>&1 | tail -1
    (cd "$AUDIOTEE_TMP/audiotee" && swift build -c release -Xswiftc -suppress-warnings 2>&1 | grep -E "error:|Build complete")
    cp "$AUDIOTEE_TMP/audiotee/.build/release/audiotee" "$AUDIOTEE_BIN"
    chmod +x "$AUDIOTEE_BIN"
    # Ad-hoc sign so macOS (especially Tahoe/16+) can anchor a TCC privacy entry
    # to audiotee directly, rather than relying on the terminal's permission.
    if codesign --sign - --force "$AUDIOTEE_BIN" 2>/dev/null; then
      echo "[✓] audiotee signed (ad-hoc) for macOS privacy permissions."
    elif sudo codesign --sign - --force "$AUDIOTEE_BIN" 2>/dev/null; then
      echo "[✓] audiotee signed (ad-hoc, via sudo) for macOS privacy permissions."
    else
      echo "[!] codesign failed (non-fatal). Audio capture may need manual permission on macOS 16+."
    fi
    rm -rf "$AUDIOTEE_TMP"
    echo "[✓] audiotee built and installed to $AUDIOTEE_BIN"
    echo
    echo "[i] audiotee captures all system audio without BlackHole or any manual setup."
    echo "    Volume control works normally. No Audio MIDI Setup changes needed."
    echo "    On first recording, macOS will ask permission for System Audio Recording."
    echo
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
  echo "[+] Creating venv at ${VENV_DIR}..."
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
    zsh) RC_FILE="$HOME/.zprofile" ;;
    bash) RC_FILE="$HOME/.bash_profile" ;;
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

echo
echo "=== Installation complete ==="
echo "Run 'meetingscribe setup' to configure API keys and audio device."
