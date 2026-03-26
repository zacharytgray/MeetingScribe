from __future__ import annotations

import datetime
import difflib
import re
import threading
from pathlib import Path
from typing import Callable, Optional

from .config import Config
from .recorder import (
    AudioRecorder, AudioTeeRecorder,
    audiotee_available, macos_version,
)
from .transcriber import Transcriber, TranscriptSegment, _fmt_time
from .summarizer import save_raw_transcript, save_summary, summarize


class MeetingSession:
    """
    Orchestrates AudioRecorder + Transcriber + Summarizer for one recording session.

    Optionally runs a second parallel stream for microphone input, attributing
    all mic segments to config.user_name without diarization.
    """

    def __init__(
        self,
        config: Config,
        on_segment: Optional[Callable[[TranscriptSegment], None]] = None,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._config = config
        self._on_segment = on_segment
        self._on_status = on_status

        self._recorder: Optional[AudioRecorder | AudioTeeRecorder] = None
        self._mic_recorder: Optional[AudioRecorder] = None
        self._start_time: Optional[datetime.datetime] = None
        self._end_time: Optional[datetime.datetime] = None
        self._lock = threading.Lock()
        self._running = False

        # Loopback transcriber (diarization enabled if configured)
        self._transcriber = Transcriber(
            whisper_model=config.whisper_model,
            use_diarization=config.use_diarization,
            hf_token=config.effective_hf_token,
            chunk_queue=None,  # wired in start()
            on_segment=self._on_segment,
            language="en",
            diarization_threshold=config.diarization_threshold,
            speaker_tracker_threshold=config.speaker_tracker_threshold,
        )

        # Mic transcriber — only created when mic_device_index is set
        self._mic_transcriber: Optional[Transcriber] = None
        if config.mic_device_index is not None:
            self._mic_transcriber = Transcriber(
                whisper_model=config.whisper_model,
                use_diarization=False,
                hf_token="",
                chunk_queue=None,  # wired in start()
                on_segment=self._on_segment,
                language="en",
                default_speaker=config.user_name,
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_models(self) -> None:
        """Blocking. Load Whisper (and pyannote if enabled) into memory."""
        self._emit_status("Loading models…")
        self._transcriber.load_models()
        if self._mic_transcriber is not None:
            self._mic_transcriber.load_models()
        self._emit_status("Models ready.")

    def start(self) -> None:
        """Start recording. Models must already be loaded via load_models()."""
        with self._lock:
            if self._running:
                return
            self._running = True

        self._start_time = datetime.datetime.now()

        # Loopback stream — pick backend based on config.audio_backend
        self._recorder = _make_loopback_recorder(self._config)
        self._transcriber.chunk_queue = self._recorder.chunk_queue
        self._transcriber.start()
        self._recorder.start()

        backend_label = "audiotee" if isinstance(self._recorder, AudioTeeRecorder) else "sounddevice"
        self._emit_status(f"Recording started (backend: {backend_label}).")

        # Mic stream (optional) — always uses sounddevice
        if self._mic_transcriber is not None:
            self._mic_recorder = AudioRecorder(
                device_index=self._config.mic_device_index,
                chunk_seconds=self._config.chunk_seconds,
            )
            self._mic_transcriber.chunk_queue = self._mic_recorder.chunk_queue
            self._mic_transcriber.start()
            self._mic_recorder.start()

    def stop(self) -> Optional[Path]:
        """
        Stop recording, drain transcription queues, summarize, and save.
        Returns the path to the saved file, or None if transcript is empty.
        """
        with self._lock:
            if not self._running:
                return None
            self._running = False

        self._emit_status("Stopping recording…")
        self._recorder.stop()
        if self._mic_recorder is not None:
            self._mic_recorder.stop()

        self._emit_status("Transcribing final chunk…")
        self._transcriber.stop()
        if self._mic_transcriber is not None:
            self._mic_transcriber.stop()

        self._end_time = datetime.datetime.now()

        transcript = self._merge_transcripts()
        if not transcript.strip():
            self._emit_status("No speech detected; nothing saved.")
            self._recorder.cleanup()
            if self._mic_recorder is not None:
                self._mic_recorder.cleanup()
            return None

        duration = (self._end_time - self._start_time).total_seconds()
        output_dir = self._config.resolved_output_dir
        active = self._config.active_providers
        _provider_name = active[0].capitalize() if active else None

        saved_path: Optional[Path] = None
        if _provider_name:
            try:
                self._emit_status(f"Summarizing with {_provider_name}…")
                slug, markdown = summarize(
                    transcript=transcript,
                    api_key=self._config.effective_api_key,
                    meeting_date=self._start_time,
                    duration_seconds=duration,
                    openrouter_api_key=self._config.effective_openrouter_key,
                    openrouter_model=self._config.openrouter_model,
                    openai_api_key=self._config.effective_openai_key,
                    openai_model=self._config.openai_model,
                    gemini_api_key=self._config.effective_gemini_key,
                    gemini_model=self._config.gemini_model,
                    ollama_host=self._config.ollama_host,
                    ollama_model=self._config.ollama_model,
                    provider_order=self._config.provider_order,
                    user_name=self._config.user_name if self._config.mic_device_index is not None else "",
                )
                saved_path = save_summary(slug, markdown, output_dir, self._start_time, transcript=transcript)
                self._emit_status(f"Saved: {saved_path}")
            except Exception as e:
                self._emit_status(f"{_provider_name} error ({e}); saving raw transcript.")
                saved_path = save_raw_transcript(transcript, output_dir, self._start_time)
        else:
            self._emit_status("No summarization provider configured; saving raw transcript.")
            saved_path = save_raw_transcript(transcript, output_dir, self._start_time)

        self._recorder.cleanup()
        if self._mic_recorder is not None:
            self._mic_recorder.cleanup()
        return saved_path

    def get_live_transcript(self) -> str:
        return self._merge_transcripts()

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _merge_transcripts(self) -> str:
        """Merge loopback and mic segments, sorted by start time."""
        raw_loopback = self._transcriber.get_segments()
        mic_segs = self._mic_transcriber.get_segments() if self._mic_transcriber is not None else []

        # When mic is active, any unlabeled loopback segment is "Remote".
        # Build new TranscriptSegment objects rather than mutating the originals,
        # which are still referenced by the transcriber's internal list.
        if self._mic_transcriber is not None:
            loopback_segs = [
                TranscriptSegment(s.start, s.end, s.text, "Remote") if s.speaker is None else s
                for s in raw_loopback
            ]
        else:
            loopback_segs = raw_loopback

        segments = loopback_segs + mic_segs

        # Remove mic segments that are acoustic echoes of loopback audio
        # (mic picking up speaker output — identical text at the same timestamp)
        if self._mic_transcriber is not None:
            segments = _remove_echo_segments(
                segments,
                user_name=self._config.user_name,
            )

        segments.sort(key=lambda s: s.start)

        parts = []
        for seg in segments:
            prefix = f"[{seg.speaker}] " if seg.speaker else ""
            ts = f"[{_fmt_time(seg.start)}–{_fmt_time(seg.end)}]"
            parts.append(f"{ts} {prefix}{seg.text.strip()}")
        return "\n".join(parts)

    def _emit_status(self, msg: str) -> None:
        if self._on_status:
            self._on_status(msg)


def _make_loopback_recorder(config: Config) -> "AudioRecorder | AudioTeeRecorder":
    """
    Return the appropriate loopback recorder based on config.audio_backend.

    "auto"        → AudioTeeRecorder on macOS 14.2+ with audiotee in PATH,
                    AudioRecorder otherwise
    "audiotee"    → AudioTeeRecorder (raises RuntimeError if audiotee missing)
    "sounddevice" → AudioRecorder always
    """
    backend = config.audio_backend
    if backend == "audiotee":
        if not audiotee_available():
            raise RuntimeError(
                "audio_backend is set to 'audiotee' but the audiotee binary was not found in PATH. "
                "Build it with: git clone https://github.com/makeusabrew/audiotee && "
                "cd audiotee && swift build -c release && "
                "cp .build/release/audiotee /usr/local/bin/  "
                "Or change audio_backend to 'auto' or 'sounddevice' in config."
            )
        return AudioTeeRecorder(chunk_seconds=config.chunk_seconds)
    if backend == "auto" and macos_version() >= (14, 2) and audiotee_available():
        return AudioTeeRecorder(chunk_seconds=config.chunk_seconds)
    return AudioRecorder(
        device_index=config.audio_device_index,
        chunk_seconds=config.chunk_seconds,
    )


# Strip punctuation before tokenizing so "ports," == "ports" and "5." == "5".
_PUNCT_RE = re.compile(r"[^\w\s]")


def _tokenize(text: str) -> set[str]:
    """Lower-case, strip punctuation, return word set."""
    return set(_PUNCT_RE.sub("", text.lower()).split())


def _word_overlap(a: str, b: str) -> float:
    """Fraction of the shorter segment's words that appear in the other.

    Punctuation is stripped before comparison so Whisper's trailing periods
    and commas don't prevent matching otherwise identical words.
    """
    wa = _tokenize(a)
    wb = _tokenize(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


def _remove_echo_segments(
    segments: list[TranscriptSegment],
    user_name: str,
    time_window: float = 8.0,
    similarity_threshold: float = 0.65,
    sequence_threshold: float = 0.50,
) -> list[TranscriptSegment]:
    """
    Drop mic segments that are acoustic echoes of loopback audio.

    When speakers are used (no headphones), the microphone picks up audio
    playing through the speakers, producing near-duplicate segments at the
    same timestamp.  We identify echoes by comparing every user-labeled
    segment against every loopback segment using two criteria:

    1. **Temporal proximity** — the actual gap between segments must be ≤
       time_window seconds.  The default 8 s covers timing drift that
       accumulates between the two recording streams over several minutes:
       the mic uses a 500 ms poll loop that can overshoot each 30 s chunk
       by up to 500 ms, compounding to ~5 s drift after 10 chunks.  The
       larger window ensures echoes are still detected late in a session.

       (Previous versions used ``min(|start_a − start_b|, |end_a − end_b|)``
       which is not the temporal gap between segments and produced
       falsely-large values whenever segment lengths differed.)

    2. **Text similarity** — either word-set overlap ≥ similarity_threshold
       OR ``difflib.SequenceMatcher.ratio()`` ≥ sequence_threshold.  Using
       both catches cases where Whisper transcribes the echo with different
       punctuation, filler words, or minor word substitutions.
    """
    user_segs = {id(s): s for s in segments if s.speaker == user_name}
    other_segs = [s for s in segments if s.speaker != user_name]

    echo_ids: set[int] = set()
    for uid, user_seg in user_segs.items():
        for other_seg in other_segs:
            # Actual temporal gap: 0 when segments overlap, positive otherwise.
            gap = max(
                0.0,
                max(user_seg.start, other_seg.start) - min(user_seg.end, other_seg.end),
            )
            if gap > time_window:
                continue

            # Primary check: punctuation-stripped word-set overlap.
            if _word_overlap(user_seg.text, other_seg.text) >= similarity_threshold:
                echo_ids.add(uid)
                break

            # Fallback: character-level sequence ratio catches near-duplicates
            # that share few *distinct* words but are textually very similar
            # (e.g. Whisper insertions/deletions of filler words).
            ratio = difflib.SequenceMatcher(
                None,
                user_seg.text.lower(),
                other_seg.text.lower(),
            ).ratio()
            if ratio >= sequence_threshold:
                echo_ids.add(uid)
                break

    return [s for s in segments if id(s) not in echo_ids]
