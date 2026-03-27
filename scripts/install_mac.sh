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
  # Install to ~/.local/bin (same dir as meetingscribe launchers) — no sudo needed.
  mkdir -p "$BIN_DIR"
  AUDIOTEE_BIN="$BIN_DIR/audiotee"
  if command -v audiotee &>/dev/null; then
    echo "[✓] audiotee already installed."
    # macOS 16 (Tahoe) requires the binary to be code-signed so macOS can
    # anchor a TCC privacy entry to it. Without this, audiotee may run without
    # errors but receive only silence.
    # Only sign if not already signed — re-signing an already-signed binary
    # can invalidate the existing TCC entry, forcing the user to re-grant
    # System Audio Recording permission from scratch.
    AUDIOTEE_PATH="$(command -v audiotee)"
    # Always sign with --force. Ad-hoc re-signing the same binary content
    # produces the same CDHash, so existing TCC entries remain valid.
    if codesign --sign - --force "$AUDIOTEE_PATH" 2>/dev/null; then
      echo "[✓] audiotee signed (ad-hoc) for macOS privacy permissions."
    elif sudo codesign --sign - --force "$AUDIOTEE_PATH" 2>/dev/null; then
      echo "[✓] audiotee signed (ad-hoc, via sudo) for macOS privacy permissions."
    else
      echo "[!] codesign failed. Run manually: codesign --sign - --force \$(which audiotee)"
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
    echo "[✓] audiotee built and ready for system audio capture."
    echo "    No BlackHole, no Audio MIDI Setup, volume works normally."
    echo
  fi

  # --- Grant System Audio Recording permission ---
  # audiotee needs Screen & System Audio Recording permission in System Settings.
  # This must be done once, manually — there is no programmatic way to grant it.
  AUDIOTEE_PATH="$(command -v audiotee 2>/dev/null || echo "$AUDIOTEE_BIN")"
  echo "┌─────────────────────────────────────────────────────────┐"
  echo "│  IMPORTANT: Grant audiotee System Audio Recording       │"
  echo "│                                                         │"
  echo "│  1. System Settings will open to the right page.        │"
  echo "│  2. Click '+' and navigate to:                          │"
  echo "│     $AUDIOTEE_PATH"
  echo "│  3. Toggle it ON.                                       │"
  echo "│                                                         │"
  echo "│  Without this, audiotee will produce silence.           │"
  echo "└─────────────────────────────────────────────────────────┘"
  echo
  read -rp "Open System Settings now? [Y/n]: " OPEN_SETTINGS
  OPEN_SETTINGS="${OPEN_SETTINGS:-y}"
  if [[ "$OPEN_SETTINGS" =~ ^[Yy] ]]; then
    open "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
    echo "[i] System Settings opened. Add audiotee and toggle it ON."
    echo "    Press Enter once you've granted the permission…"
    read -r
  fi
  echo
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
# 3. Create Python venv  (requires 3.10–3.13; PyTorch has no 3.14+ wheels yet)
# ---------------------------------------------------------------------------
PYTHON_BIN=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" &>/dev/null; then
    ver=$("$candidate" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
    major=${ver%%.*}; minor=${ver##*.}
    if [ "$major" -eq 3 ] && [ "$minor" -ge 10 ] && [ "$minor" -le 13 ]; then
      PYTHON_BIN="$candidate"
      PYTHON_VERSION="$ver"
      break
    fi
  fi
done

if [ -z "$PYTHON_BIN" ]; then
  echo "[!] No compatible Python found (need 3.10–3.13; PyTorch has no wheels for 3.14+ yet)."
  echo "    Install Python 3.12 with:  brew install python@3.12"
  echo "    Then re-run this script."
  exit 1
fi

echo "[+] Using Python $PYTHON_VERSION ($PYTHON_BIN)"

if [ -d "$VENV_DIR" ]; then
  # Verify the existing venv's Python is compatible (may have been created with
  # a now-incompatible version, e.g. 3.14 before PyTorch added wheels for it).
  VENV_PY_VER=$("$VENV_DIR/bin/python" -c \
    "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
  VENV_MINOR=${VENV_PY_VER##*.}
  VENV_MAJOR=${VENV_PY_VER%%.*}
  if [ "$VENV_MAJOR" -ne 3 ] || [ "$VENV_MINOR" -lt 10 ] || [ "$VENV_MINOR" -gt 13 ]; then
    echo "[!] Existing venv uses Python $VENV_PY_VER (incompatible). Removing and recreating with ${PYTHON_VERSION}..."
    rm -rf "$VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
    echo "[✓] Venv recreated at $VENV_DIR."
  else
    echo "[✓] Venv already exists at $VENV_DIR (Python $VENV_PY_VER)."
  fi
else
  echo "[+] Creating venv at ${VENV_DIR}..."
  "$PYTHON_BIN" -m venv "$VENV_DIR"
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
