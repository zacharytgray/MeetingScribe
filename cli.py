#!/usr/bin/env python3
"""MeetingScribe CLI entry point. Run directly: python cli.py <command>"""
from __future__ import annotations

import os
# Must be set before torch/ctranslate2 load their OpenMP runtimes.
# On macOS both PyTorch and ctranslate2 bundle libiomp5.dylib which conflicts.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

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

    # Anthropic API key
    val = input(f"Anthropic API key [{_mask(cfg.anthropic_api_key)}]: ").strip()
    if val:
        cfg.anthropic_api_key = val

    # OpenRouter API key
    print(f"\nOpenRouter is a free alternative for summarization (free models available).")
    print(f"Get a key at openrouter.ai — leave blank to skip.")
    val = input(f"OpenRouter API key [{_mask(cfg.openrouter_api_key)}]: ").strip()
    if val:
        cfg.openrouter_api_key = val
    if cfg.openrouter_api_key:
        val = input(f"OpenRouter model [{cfg.openrouter_model}]: ").strip()
        if val:
            cfg.openrouter_model = val

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

    # Audio device
    print("\nAvailable input devices:")
    devices = list_devices()
    loopback_idx = find_loopback_device()
    for dev in devices:
        marker = " ← recommended (loopback)" if dev["index"] == loopback_idx else ""
        print(f"  {dev['index']}: {dev['name']}{marker}")
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
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
