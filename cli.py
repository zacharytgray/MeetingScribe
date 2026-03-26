#!/usr/bin/env python3
"""MeetingScribe CLI entry point. Primary usage: meetingscribe <command>."""
from __future__ import annotations

import os
# Must be set before torch/ctranslate2 load their OpenMP runtimes.
# On macOS both PyTorch and ctranslate2 bundle libiomp5.dylib which conflicts.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
# Prevent PyTorch from using MPS (Metal) on macOS — pyannote can segfault
# during model load when MPS initializes alongside ctranslate2.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import argparse
import sys
import threading

# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------

RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
BOLD = "\033[1m"
RESET = "\033[0m"


def c(color: str, text: str) -> str:
    return f"{color}{text}{RESET}"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_devices(_args: argparse.Namespace) -> None:
    from meetingscribe.recorder import list_devices, find_loopback_device
    devices = list_devices()
    if not devices:
        print(c(YELLOW, "No input devices found."))
        return
    loopback_idx = find_loopback_device()
    print(c(BOLD, f"{'#':<4} {'Name':<50} {'Channels'}"))
    print("-" * 65)
    for dev in devices:
        marker = c(GREEN, " ← loopback") if dev["index"] == loopback_idx else ""
        print(f"{dev['index']:<4} {dev['name']:<50} {dev['channels']}{marker}")


def cmd_config(_args: argparse.Namespace) -> None:
    from meetingscribe.config import load_config
    import dataclasses, json
    cfg = load_config()
    print(json.dumps(dataclasses.asdict(cfg), indent=2))


def cmd_setup(_args: argparse.Namespace) -> None:
    from meetingscribe.config import load_config, save_config, CONFIG_FILE
    from meetingscribe.recorder import list_devices, find_loopback_device

    print(c(BOLD, "\n=== MeetingScribe Setup ===\n"))
    cfg = load_config()

    # Output directory
    val = input(f"Notes output directory [{cfg.output_dir}]: ").strip().strip("'\"")
    if val:
        cfg.output_dir = val

    # -------------------------------------------------------------------------
    # Summarization providers — pick which ones to configure
    # -------------------------------------------------------------------------
    from meetingscribe.config import KNOWN_PROVIDERS
    print(c(BOLD, "\n--- Summarization Provider ---"))
    print("MeetingScribe summarizes your transcript using an AI provider.")
    print("Audio stays local; only the text transcript is sent to the chosen provider.\n")
    _provider_menu = [
        ("anthropic",  "Anthropic (Claude)   — paid, highest quality"),
        ("openai",     "OpenAI (GPT)         — paid"),
        ("gemini",     "Google Gemini        — paid, generous free tier"),
        ("openrouter", "OpenRouter           — free models available"),
        ("ollama",     "Ollama               — fully local, no API key"),
        (None,         "None / skip"),
    ]
    _currently_configured = cfg.active_providers
    if _currently_configured:
        print(f"  Currently configured: {', '.join(_currently_configured)}")
    for i, (_, label) in enumerate(_provider_menu, 1):
        print(f"  {i}. {label}")
    print()
    while True:
        raw = input("Select providers (comma-separated numbers, or Enter to keep current): ").strip()
        if not raw:
            _selected_providers = [p for p, _ in _provider_menu if p is not None and p in (cfg.active_providers or [])]
            # If nothing was configured before either, just break and skip
            break
        parts = [p.strip() for p in raw.split(",")]
        if all(p.isdigit() and 1 <= int(p) <= len(_provider_menu) for p in parts):
            chosen_indices = [int(p) - 1 for p in parts]
            _selected_providers = [_provider_menu[i][0] for i in chosen_indices if _provider_menu[i][0] is not None]
            # Clear keys for providers that were deselected
            if "anthropic" not in _selected_providers:
                cfg.anthropic_api_key = ""
            if "openrouter" not in _selected_providers:
                cfg.openrouter_api_key = ""
            if "openai" not in _selected_providers:
                cfg.openai_api_key = ""
            if "gemini" not in _selected_providers:
                cfg.gemini_api_key = ""
            if "ollama" not in _selected_providers:
                cfg.ollama_model = ""
            break
        print(c(YELLOW, f"  Enter numbers 1–{len(_provider_menu)}, e.g. 1,4"))

    # Prompt for credentials / model only for selected providers
    if "anthropic" in _selected_providers:
        print()
        val = input(f"  Anthropic API key [{_mask(cfg.anthropic_api_key)}]: ").strip()
        if val:
            cfg.anthropic_api_key = val

    if "openai" in _selected_providers:
        print()
        val = input(f"  OpenAI API key [{_mask(cfg.openai_api_key)}]: ").strip()
        if val:
            cfg.openai_api_key = val
        val = input(f"  OpenAI model [{cfg.openai_model}]: ").strip()
        if val:
            cfg.openai_model = val

    if "gemini" in _selected_providers:
        print()
        print(f"  Get a free Gemini key at aistudio.google.com")
        val = input(f"  Gemini API key [{_mask(cfg.gemini_api_key)}]: ").strip()
        if val:
            cfg.gemini_api_key = val
        val = input(f"  Gemini model [{cfg.gemini_model}]: ").strip()
        if val:
            cfg.gemini_model = val

    if "openrouter" in _selected_providers:
        print()
        print(f"  Get a free OpenRouter key at openrouter.ai")
        val = input(f"  OpenRouter API key [{_mask(cfg.openrouter_api_key)}]: ").strip()
        if val:
            cfg.openrouter_api_key = val
        val = input(f"  OpenRouter model [{cfg.openrouter_model}]: ").strip()
        if val:
            cfg.openrouter_model = val

    if "ollama" in _selected_providers:
        print()
        print(f"  Ollama runs models locally — get it at ollama.ai")
        val = input(f"  Ollama host [{cfg.ollama_host}]: ").strip()
        if val:
            cfg.ollama_host = val
        val = input(f"  Ollama model (e.g. llama3.2, mistral) [{cfg.ollama_model or 'llama3.2'}]: ").strip()
        cfg.ollama_model = val or cfg.ollama_model or "llama3.2"

    # Provider order (only relevant if ≥2 configured)
    _active_now = [p for p in KNOWN_PROVIDERS if (
        (p == "anthropic"  and cfg.anthropic_api_key) or
        (p == "openai"     and cfg.openai_api_key) or
        (p == "gemini"     and cfg.gemini_api_key) or
        (p == "openrouter" and cfg.openrouter_api_key) or
        (p == "ollama"     and cfg.ollama_model)
    )]
    if len(_active_now) >= 2:
        _current_order = [p for p in cfg.provider_order if p in _active_now]
        # Append any newly added providers not yet in order
        for p in _active_now:
            if p not in _current_order:
                _current_order.append(p)
        print()
        print(c(BOLD, "--- Provider Priority ---"))
        print(f"When multiple providers are configured, MeetingScribe uses the first in order.")
        print(f"  Current order: {', '.join(_current_order)}")
        raw = input("  New order (comma-separated names, or Enter to keep): ").strip()
        if raw:
            parts = [p.strip().lower() for p in raw.split(",")]
            valid = [p for p in parts if p in KNOWN_PROVIDERS]
            invalid = [p for p in parts if p not in KNOWN_PROVIDERS]
            if invalid:
                print(c(YELLOW, f"  Unknown providers ignored: {', '.join(invalid)}"))
            if valid:
                # Put valid entries first, then append any active providers not mentioned
                new_order = valid + [p for p in _current_order if p not in valid]
                cfg.provider_order = new_order
                print(c(GREEN, f"  Provider order set: {', '.join(new_order)}"))
        else:
            cfg.provider_order = _current_order

    # HuggingFace token
    val = input(f"HuggingFace token (for speaker diarization) [{_mask(cfg.hf_token)}]: ").strip()
    if val:
        cfg.hf_token = val

    # Whisper model
    valid_models = ["tiny", "base", "small", "medium", "large-v3"]
    print(f"\nWhisper model — choose one: {', '.join(valid_models)}")
    while True:
        val = input(f"Whisper model [{cfg.whisper_model}]: ").strip()
        if not val:
            break  # keep current
        if val in valid_models:
            cfg.whisper_model = val
            break
        print(c(YELLOW, f"  Invalid model '{val}'. Choose from: {', '.join(valid_models)}"))

    # Diarization
    val = input(f"Enable speaker diarization? [{'y' if cfg.use_diarization else 'n'}]: ").strip().lower()
    if val in ("y", "yes"):
        cfg.use_diarization = True
    elif val in ("n", "no"):
        cfg.use_diarization = False

    # Audio capture backend (macOS-only)
    import sys, platform as _platform
    _is_mac = sys.platform == "darwin"
    if _is_mac:
        from meetingscribe.recorder import audiotee_available, macos_version
        _mac_ver = macos_version()
        _audiotee_ok = audiotee_available()
        print(c(BOLD, "\n--- Audio Capture Backend ---"))
        if _mac_ver >= (14, 2):
            import shutil as _shutil
            _swift_ok = _shutil.which("swift") is not None
            if _audiotee_ok:
                print(c(GREEN, "  audiotee detected — driver-free capture is available (recommended)."))
                if _swift_ok:
                    ans = input("  Rebuild audiotee from source? (fixes macOS SDK incompatibilities) [y/N]: ").strip().lower()
                    if ans in ("y", "yes"):
                        _audiotee_ok = _build_audiotee()
            else:
                print(f"  macOS {_mac_ver[0]}.{_mac_ver[1]} supports driver-free audio capture via audiotee")
                print(f"  (no BlackHole, no Audio MIDI Setup, volume works normally).")
                print()
                if not _swift_ok:
                    print(c(YELLOW, "  [!] Swift not found — needed to build audiotee."))
                    print(c(YELLOW, "      Install Xcode Command Line Tools, then re-run setup:"))
                    print(c(YELLOW, "          xcode-select --install"))
                else:
                    ans = input("  Build and install audiotee now? (~90s) [Y/n]: ").strip().lower()
                    if ans in ("", "y", "yes"):
                        _audiotee_ok = _build_audiotee()
        else:
            print(f"  macOS {_mac_ver[0]}.{_mac_ver[1]} — audiotee requires macOS 14.2+. Using BlackHole.")
        print()
        print("  auto        — use audiotee on macOS 14.2+ if available, BlackHole otherwise (default)")
        print("  sounddevice — always use BlackHole (for older macOS or troubleshooting)")
        print("  audiotee    — always use audiotee (error if not installed)")
        valid_backends = ["auto", "sounddevice", "audiotee"]
        while True:
            val = input(f"  Audio backend [{cfg.audio_backend}]: ").strip().lower()
            if not val:
                break
            if val in valid_backends:
                cfg.audio_backend = val
                break
            print(c(YELLOW, f"  Choose one of: {', '.join(valid_backends)}"))

    # Audio device (only relevant for sounddevice backend)
    _show_device_prompt = not _is_mac or cfg.audio_backend in ("sounddevice",) or (cfg.audio_backend == "auto" and not (_is_mac and audiotee_available() if _is_mac else False))
    print("\nAvailable input devices:")
    devices = list_devices()
    loopback_idx = find_loopback_device()
    for dev in devices:
        marker = " ← recommended (loopback)" if dev["index"] == loopback_idx else ""
        print(f"  {dev['index']}: {dev['name']}{marker}")
    if _is_mac and cfg.audio_backend == "auto" and audiotee_available() and macos_version() >= (14, 2):
        print(c(CYAN, "  (audiotee backend active — audio device index only used as fallback)"))
    auto_str = f"auto (detected: {loopback_idx})" if loopback_idx is not None else "auto (none detected)"
    val = input(f"Audio device index [{cfg.audio_device_index if cfg.audio_device_index is not None else auto_str}]: ").strip()
    if val.isdigit():
        cfg.audio_device_index = int(val)
    elif val == "":
        pass  # keep current

    # Microphone device
    print("\nMicrophone capture attributes your own voice as a separate speaker.")
    print("Leave blank to disable (mic capture is optional).")
    mic_current = str(cfg.mic_device_index) if cfg.mic_device_index is not None else "disabled"
    val = input(f"Microphone device index [{mic_current}]: ").strip()
    if val.isdigit():
        cfg.mic_device_index = int(val)
    elif val.lower() in ("none", "disable", "disabled", ""):
        cfg.mic_device_index = None

    # User name (shown in transcript for mic audio)
    if cfg.mic_device_index is not None:
        val = input(f"Your name in transcript [{cfg.user_name}]: ").strip()
        if val:
            cfg.user_name = val

    # Chunk duration
    print(c(BOLD, "\n--- Transcription Chunk Duration ---"))
    print("Longer chunks give diarization more audio context (better accuracy)")
    print("but delay when the first transcript line appears.")
    print("  30s — low latency, good for most uses (default)")
    print("  60s — better speaker separation, 60s before first line")
    print("  90s — best quality, 90s before first line")
    while True:
        val = input(f"Chunk duration in seconds [{cfg.chunk_seconds}]: ").strip()
        if not val:
            break
        if val.isdigit() and int(val) > 0:
            cfg.chunk_seconds = int(val)
            break
        print(c(YELLOW, "  Enter a positive integer (e.g. 30, 60, 90)."))

    # Diarization thresholds
    if cfg.use_diarization:
        print(c(BOLD, "\n--- Diarization Sensitivity ---"))
        print("These thresholds control how aggressively speakers are merged.")
        print("Lower values merge more → fewer false splits, but may blend distinct voices.")
        print()
        print(f"  {'Preset':<20} {'Speakers':<12} {'cluster':<10} {'tracker':<10} {'chunk'}")
        print(f"  {'-'*20} {'-'*12} {'-'*10} {'-'*10} {'-'*6}")
        print(f"  {'1-on-1':<20} {'2':<12} {'0.45':<10} {'0.60':<10} {'60s'}")
        print(f"  {'Small team':<20} {'3–4':<12} {'0.55':<10} {'0.65':<10} {'30s  ← default'}")
        print(f"  {'Medium meeting':<20} {'5–7':<12} {'0.65':<10} {'0.70':<10} {'30s'}")
        print(f"  {'Large meeting':<20} {'8+':<12} {'0.72':<10} {'0.75':<10} {'30s'}")
        print()
        print("  cluster = pyannote within-chunk merging threshold")
        print("  tracker = cross-chunk speaker identity threshold")
        print()
        presets = {
            "1": (0.45, 0.60, 60,  "1-on-1"),
            "2": (0.55, 0.65, 30,  "Small team (default)"),
            "3": (0.65, 0.70, 30,  "Medium meeting"),
            "4": (0.72, 0.75, 30,  "Large meeting"),
        }
        print("  Enter 1–4 to apply a preset, or press Enter to keep current values.")
        val = input(f"  Preset [current: cluster={cfg.diarization_threshold}, tracker={cfg.speaker_tracker_threshold}]: ").strip()
        if val in presets:
            dt, st, cs, label = presets[val]
            cfg.diarization_threshold = dt
            cfg.speaker_tracker_threshold = st
            cfg.chunk_seconds = cs
            print(c(GREEN, f"  Applied preset: {label}"))
        elif val == "":
            pass
        else:
            print(c(YELLOW, "  Unrecognised preset, keeping current values."))

    save_config(cfg)
    print(c(GREEN, f"\nConfig saved to {CONFIG_FILE}\n"))


def cmd_test_audio(args: argparse.Namespace) -> None:
    import tempfile
    import numpy as np
    import sounddevice as sd
    import soundfile as sf
    from meetingscribe.recorder import list_devices, SAMPLE_RATE

    duration = args.duration
    device = args.device

    # Show device name
    all_devices = list_devices()
    dev_name = next((d["name"] for d in all_devices if d["index"] == device), f"device {device}")
    print(c(BOLD, f"\nRecording {duration}s from: [{device}] {dev_name}"))
    print("Speak or play audio now…\n")

    try:
        audio = sd.rec(
            int(duration * SAMPLE_RATE),
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            device=device,
        )
        sd.wait()
    except Exception as e:
        print(c(RED, f"Failed to record from device {device}: {e}"))
        return

    audio = audio.flatten()
    peak = float(np.abs(audio).max())
    mean = float(np.abs(audio).mean())
    rms = float(np.sqrt(np.mean(audio ** 2)))

    print(f"  Samples captured : {len(audio):,}")
    print(f"  Peak amplitude   : {peak:.6f}")
    print(f"  Mean amplitude   : {mean:.6f}")
    print(f"  RMS              : {rms:.6f}")
    print(f"  Silence threshold: 0.001000\n")

    if peak < 0.0001:
        print(c(RED, "RESULT: Complete silence — device is not receiving any audio signal."))
        print("        Either the device is wrong, or nothing is routing audio through it.")
    elif mean < 0.001:
        print(c(YELLOW, "RESULT: Very low signal — audio is present but extremely quiet."))
        print("        Whisper's VAD filter will discard this as silence.")
        print("        Try increasing system volume or check audio routing.")
    else:
        print(c(GREEN, "RESULT: Audio signal detected — device is working."))
        print("        If transcription still fails, the issue is in Whisper, not capture.")

    # Optionally save WAV for listening
    if args.save:
        with tempfile.NamedTemporaryFile(suffix=".wav", prefix="meetingscribe_test_", delete=False) as f:
            tmp = f.name
        sf.write(tmp, audio, SAMPLE_RATE)
        print(f"\nSaved WAV to: {tmp}")
        import platform, subprocess
        if platform.system() == "Darwin":
            subprocess.Popen(["open", tmp])


def cmd_cleanup(_args: argparse.Namespace) -> None:
    from meetingscribe.recorder import cleanup_audiotee
    print(c(BOLD, "\nCleaning up persistent audiotee processes…\n"))
    cleanup_audiotee()
    print()


def cmd_start(args: argparse.Namespace) -> None:
    from meetingscribe.config import load_config
    from meetingscribe.session import MeetingSession
    from meetingscribe.transcriber import TranscriptSegment

    cfg = load_config()

    # CLI overrides
    if args.model:
        cfg.whisper_model = args.model
    if args.output:
        cfg.output_dir = args.output
    if args.device is not None:
        cfg.audio_device_index = args.device
    if args.no_diarization:
        cfg.use_diarization = False

    def on_segment(seg: TranscriptSegment) -> None:
        prefix = c(CYAN, f"[{seg.speaker}] ") if seg.speaker else ""
        print(f"  {prefix}{seg.text.strip()}")

    def on_status(msg: str) -> None:
        print(c(YELLOW, f"[status] {msg}"))

    session = MeetingSession(cfg, on_segment=on_segment, on_status=on_status)

    print(c(BOLD, "\nLoading models (this may take a minute first time)…"))
    try:
        session.load_models()
    except Exception as e:
        print(c(RED, f"Failed to load models: {e}"))
        sys.exit(1)

    print(c(GREEN, "Models loaded. Starting recording…"))
    session.start()
    print(c(BOLD, "\nRecording. Commands: [s]top  [t]ranscript  [q]uit  [h]elp\n"))

    # Start background model-loading display
    try:
        _run_input_loop(session)
    except KeyboardInterrupt:
        print(c(YELLOW, "\nCtrl+C — stopping…"))
        _do_stop(session)


def _run_input_loop(session) -> None:
    from meetingscribe.session import MeetingSession

    while True:
        try:
            cmd = input().strip().lower()
        except EOFError:
            break

        if cmd in ("s", "stop"):
            _do_stop(session)
            break
        elif cmd in ("t", "transcript"):
            transcript = session.get_live_transcript()
            if transcript:
                print(c(CYAN, "\n--- Live Transcript ---"))
                print(transcript)
                print(c(CYAN, "--- End ---\n"))
            else:
                print(c(YELLOW, "(no transcript yet)"))
        elif cmd in ("q", "quit"):
            print(c(YELLOW, "Quitting without saving."))
            sys.exit(0)
        elif cmd in ("h", "help", ""):
            print("  s / stop       — stop recording and summarize")
            print("  t / transcript — show live transcript")
            print("  q / quit       — exit without saving")
        else:
            print(c(YELLOW, f"Unknown command '{cmd}'. Type h for help."))


def _do_stop(session) -> None:
    path = session.stop()
    if path:
        print(c(GREEN, f"\nSaved: {path}"))
    else:
        print(c(YELLOW, "Session ended with no output."))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mask(s: str) -> str:
    if not s:
        return ""
    return s[:4] + "****" if len(s) > 4 else "****"


def _build_audiotee() -> bool:
    """
    Clone audiotee from GitHub, build with Swift, and install to /usr/local/bin/.
    Prints progress to stdout. Returns True on success, False on any failure.
    Build takes ~60–90 seconds on a typical Mac.
    """
    import shutil, subprocess, tempfile

    tmpdir = tempfile.mkdtemp(prefix="meetingscribe_audiotee_")
    clone_dir = os.path.join(tmpdir, "audiotee")
    # Install to ~/.local/bin alongside the meetingscribe launchers — no sudo needed.
    bin_dir = os.path.join(os.path.expanduser("~"), ".local", "bin")
    os.makedirs(bin_dir, exist_ok=True)
    dest = os.path.join(bin_dir, "audiotee")

    try:
        print(c(YELLOW, "  [1/3] Cloning audiotee from GitHub…"))
        result = subprocess.run(
            ["git", "clone", "--depth", "1",
             "https://github.com/makeusabrew/audiotee.git", clone_dir],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(c(RED, f"  git clone failed:\n    {result.stderr.strip()}"))
            return False

        print(c(YELLOW, "  [2/3] Building with Swift (this takes ~60–90s)…"))
        result = subprocess.run(
            ["swift", "build", "-c", "release", "-Xswiftc", "-suppress-warnings"],
            cwd=clone_dir,
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            # Show the last 1500 chars of stderr so the user sees what went wrong
            tail = result.stderr[-1500:].strip()
            print(c(RED, f"  swift build failed:\n{tail}"))
            return False

        binary = os.path.join(clone_dir, ".build", "release", "audiotee")
        if not os.path.isfile(binary):
            print(c(RED, f"  Build succeeded but binary not found at: {binary}"))
            return False

        print(c(YELLOW, f"  [3/3] Installing to {dest}…"))
        result = subprocess.run(["cp", binary, dest], capture_output=True, text=True)
        if result.returncode != 0:
            print(c(RED, f"  Install failed. Copy it manually:"))
            print(c(RED, f"      cp {binary} {dest}"))
            return False

        subprocess.run(["chmod", "+x", dest], capture_output=True)

        # Ad-hoc code-sign audiotee so macOS can anchor a TCC (privacy) entry to it.
        # Without a signing identity, macOS 15+ (Sequoia) relies on the terminal's
        # permission. macOS 16 (Tahoe) tightened this: an unsigned subprocess may
        # create the CoreAudio Tap successfully (status 0) yet receive only silence
        # because macOS has no stable identity to grant the permission to.
        # "--sign -" = ad-hoc self-signed; free, no Apple Developer account needed.
        sign_result = subprocess.run(
            ["codesign", "--sign", "-", "--force", dest],
            capture_output=True, text=True,
        )
        if sign_result.returncode == 0:
            print(c(GREEN, "  ✓ audiotee signed (ad-hoc) for macOS privacy permissions."))
        else:
            print(c(YELLOW, "  [!] codesign failed (non-fatal); audio may not work on macOS 16+:"))
            print(c(YELLOW, f"      {sign_result.stderr.strip()}"))

        print(c(GREEN, "  ✓ audiotee installed. Driver-free audio capture is now active."))
        print(c(CYAN, "  On first recording macOS will prompt for System Audio Recording"))
        print(c(CYAN, "  permission — grant it once and it will be remembered."))
        return True

    except Exception as e:
        print(c(RED, f"  Build error: {e}"))
        return False
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Arg parsing
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meetingscribe",
        description="Transcribe and summarize meetings locally + Claude API",
    )
    sub = parser.add_subparsers(dest="command")

    # setup
    sub.add_parser("setup", help="Interactive first-time configuration wizard")

    # devices
    sub.add_parser("devices", help="List available audio input devices")

    # config
    sub.add_parser("config", help="Show current configuration")

    # start
    p_start = sub.add_parser("start", help="Start a recording session")
    p_start.add_argument("-m", "--model", help="Whisper model (tiny|base|small|medium|large-v3)")
    p_start.add_argument("-o", "--output", help="Override notes output directory")
    p_start.add_argument("-d", "--device", type=int, help="Audio device index")
    p_start.add_argument("--no-diarization", action="store_true", help="Disable speaker diarization")

    # test-audio
    p_test = sub.add_parser("test-audio", help="Record a few seconds and report signal levels")
    p_test.add_argument("-d", "--device", type=int, required=True, help="Audio device index to test")
    p_test.add_argument("-t", "--duration", type=float, default=5.0, help="Seconds to record (default: 5)")
    p_test.add_argument("-s", "--save", action="store_true", help="Save WAV and open it for listening")

    # cleanup
    sub.add_parser("cleanup", help="Stop persistent audiotee process and remove state files")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "setup":
        cmd_setup(args)
    elif args.command == "devices":
        cmd_devices(args)
    elif args.command == "config":
        cmd_config(args)
    elif args.command == "start":
        cmd_start(args)
    elif args.command == "test-audio":
        cmd_test_audio(args)
    elif args.command == "cleanup":
        cmd_cleanup(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
