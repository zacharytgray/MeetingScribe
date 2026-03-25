from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


@dataclass
class TranscriptSegment:
    start: float   # seconds from session start
    end: float
    text: str
    speaker: Optional[str] = None  # e.g. "Speaker 1", "Me", or None if no diarization


class CrossChunkSpeakerTracker:
    """
    Maintains consistent global speaker identities across 30-second audio chunks
    by comparing per-speaker embeddings via cosine similarity.
    """
    def __init__(self, similarity_threshold: float = 0.65) -> None:
        self._registry: list[tuple[str, "np.ndarray"]] = []  # [(global_label, normed_embedding)]
        self._counter = 0
        self.similarity_threshold = similarity_threshold

    def resolve(self, chunk_embeddings: dict[str, "np.ndarray"]) -> dict[str, str]:
        """
        Given {chunk_local_id: embedding} for one chunk, return
        {chunk_local_id: global_label} with consistent labels across calls.
        """
        import numpy as np
        mapping: dict[str, str] = {}

        for local_id, raw_emb in chunk_embeddings.items():
            emb = np.array(raw_emb).flatten()
            norm = np.linalg.norm(emb)
            if norm < 1e-8:
                continue
            emb = emb / norm

            best_label: Optional[str] = None
            best_sim = -1.0
            for global_label, known_emb in self._registry:
                sim = float(np.dot(emb, known_emb))
                if sim > best_sim:
                    best_sim = sim
                    best_label = global_label

            if best_label is not None and best_sim >= self.similarity_threshold:
                # Same speaker — update running average
                for i, (lbl, known_emb) in enumerate(self._registry):
                    if lbl == best_label:
                        updated = (known_emb + emb) / 2
                        updated /= (np.linalg.norm(updated) + 1e-8)
                        self._registry[i] = (lbl, updated)
                        break
                mapping[local_id] = best_label
            else:
                self._counter += 1
                new_label = f"Speaker {self._counter}"
                self._registry.append((new_label, emb))
                mapping[local_id] = new_label

        return mapping


class Transcriber:
    """
    Consumes WAV paths from a queue, runs faster-whisper transcription (and
    optionally pyannote speaker diarization), and accumulates TranscriptSegments.
    """

    def __init__(
        self,
        whisper_model: str = "base",
        use_diarization: bool = True,
        hf_token: str = "",
        chunk_queue: Optional[queue.Queue] = None,
        on_segment: Optional[Callable[[TranscriptSegment], None]] = None,
        language: str = "en",
        default_speaker: Optional[str] = None,
        diarization_threshold: float = 0.55,
        speaker_tracker_threshold: float = 0.65,
    ) -> None:
        self.whisper_model_name = whisper_model
        self.use_diarization = use_diarization
        self.hf_token = hf_token
        self.chunk_queue = chunk_queue
        self.on_segment = on_segment
        self.language = language
        self.default_speaker = default_speaker  # if set, all segments get this label; diarization skipped

        self._segments: list[TranscriptSegment] = []
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._elapsed_offset: float = 0.0
        self.diarization_threshold = diarization_threshold
        self._speaker_tracker = CrossChunkSpeakerTracker(similarity_threshold=speaker_tracker_threshold)

        # Loaded lazily / explicitly via load_models()
        self._whisper = None
        self._diarizer = None
        self._embedding_inference = None  # set in load_models() if diarizer loaded

    # ------------------------------------------------------------------
    # Model loading
    # ------------------------------------------------------------------

    def load_models(self) -> None:
        """Blocking: load Whisper (and optionally pyannote) into memory."""
        import os
        if self.hf_token:
            os.environ.setdefault("HF_TOKEN", self.hf_token)

        from faster_whisper import WhisperModel
        self._whisper = WhisperModel(self.whisper_model_name, device="cpu", compute_type="int8")

        if self.use_diarization and self.hf_token:
            try:
                import huggingface_hub

                # pyannote/audio/core/pipeline.py does:
                #   from huggingface_hub import hf_hub_download   (module level)
                #   hf_hub_download(..., use_auth_token=token)    (line ~102)
                # huggingface_hub >=0.23 removed use_auth_token from hf_hub_download.
                # We must patch huggingface_hub.hf_hub_download BEFORE importing
                # pyannote so that pyannote's module-level `from ... import` picks
                # up our version.
                if not getattr(huggingface_hub.hf_hub_download, "_auth_compat_patched", False):
                    _orig_download = huggingface_hub.hf_hub_download
                    def _compat_download(*args, use_auth_token=None, **kwargs):
                        if use_auth_token is not None:
                            kwargs.setdefault("token", use_auth_token)
                        return _orig_download(*args, **kwargs)
                    _compat_download._auth_compat_patched = True
                    huggingface_hub.hf_hub_download = _compat_download

                # torchaudio >= 2.1 removed AudioMetaData from the top-level
                # namespace; pyannote.audio still references it there.
                # Try several known module paths across torchaudio versions; fall
                # back to a compatible namedtuple if nothing is importable.
                import torchaudio as _ta
                if not hasattr(_ta, "AudioMetaData"):
                    _amd_set = False
                    for _mod_path in (
                        "torchaudio.backend.common",
                        "torchaudio._backend.common",
                        "torchaudio.backend.soundfile_backend",
                    ):
                        try:
                            import importlib as _il
                            _mod = _il.import_module(_mod_path)
                            if hasattr(_mod, "AudioMetaData"):
                                _ta.AudioMetaData = _mod.AudioMetaData
                                _amd_set = True
                                break
                        except (ImportError, AttributeError):
                            continue
                    if not _amd_set:
                        from collections import namedtuple as _nt
                        _ta.AudioMetaData = _nt(
                            "AudioMetaData",
                            ["sample_rate", "num_frames", "num_channels",
                             "bits_per_sample", "encoding"],
                        )

                # torchaudio >= 2.0 removed several top-level backend functions
                # that older pyannote versions reference.
                if not hasattr(_ta, "list_audio_backends"):
                    try:
                        from torchaudio._backend import list_audio_backends as _lab
                        _ta.list_audio_backends = _lab
                    except (ImportError, AttributeError):
                        _ta.list_audio_backends = lambda: ["soundfile"]
                if not hasattr(_ta, "get_audio_backend"):
                    _ta.get_audio_backend = lambda: "soundfile"
                if not hasattr(_ta, "set_audio_backend"):
                    _ta.set_audio_backend = lambda *a, **kw: None

                # PyTorch >= 2.6 defaults torch.load to weights_only=True and
                # requires all globals to be explicitly allowlisted.
                # TorchVersion is used in pyannote model checkpoints.
                try:
                    import torch as _torch
                    import torch.torch_version as _tv_mod
                    _torch.serialization.add_safe_globals([_tv_mod.TorchVersion])
                except (AttributeError, ImportError):
                    pass

                from pyannote.audio import Pipeline, Inference

                self._diarizer = Pipeline.from_pretrained(
                    "pyannote/speaker-diarization-3.1",
                    use_auth_token=self.hf_token,
                )
                import torch
                self._diarizer.to(torch.device("cpu"))

                # Tune clustering to reduce the "same speaker → two labels" problem.
                # The default threshold (~0.70) is optimised for large diverse datasets.
                # Lowering it makes pyannote merge embeddings more aggressively, which
                # is appropriate for typical meetings with 2–4 distinct voices.
                # min_duration_off=0.0 prevents splitting a speaker on short pauses.
                try:
                    self._diarizer.instantiate({
                        "segmentation": {"min_duration_off": 0.0},
                        "clustering": {"threshold": self.diarization_threshold},
                    })
                except Exception:
                    pass

                # Set up embedding inference for cross-chunk speaker tracking
                if hasattr(self._diarizer, "embedding"):
                    self._embedding_inference = Inference(
                        self._diarizer.embedding,
                        window="whole",
                    )
                    print("[transcriber] cross-chunk speaker tracking enabled")

            except Exception as e:
                print(f"[transcriber] diarization model failed to load ({e}); continuing without diarization")
                self._diarizer = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background worker thread that drains chunk_queue."""
        if self._worker and self._worker.is_alive():
            return
        self._stop_event.clear()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True, name="transcriber-worker")
        self._worker.start()

    def stop(self) -> None:
        """Signal the worker to stop; waits until the queue is drained."""
        self._stop_event.set()
        if self._worker:
            self._worker.join(timeout=120)

    def get_full_text(self) -> str:
        """Return the formatted transcript so far."""
        with self._lock:
            parts = []
            for seg in sorted(self._segments, key=lambda s: s.start):
                prefix = f"[{seg.speaker}] " if seg.speaker else ""
                ts = f"[{_fmt_time(seg.start)}–{_fmt_time(seg.end)}]"
                parts.append(f"{ts} {prefix}{seg.text.strip()}")
            return "\n".join(parts)

    def get_segments(self) -> list[TranscriptSegment]:
        with self._lock:
            return list(self._segments)

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                item = self.chunk_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                self._process_item(item)
            except Exception as e:
                path, _ = item
                print(f"[transcriber] error processing {path}: {e}")
            finally:
                self.chunk_queue.task_done()

        # Drain remaining items after stop signal
        while True:
            try:
                item = self.chunk_queue.get_nowait()
                try:
                    self._process_item(item)
                except Exception as e:
                    print(f"[transcriber] error during final drain: {e}")
                finally:
                    self.chunk_queue.task_done()
            except queue.Empty:
                break

    def _process_item(self, item: tuple) -> None:
        path, duration = item
        if path is None:
            # Silent chunk — advance clock to keep timestamps in sync with the
            # other stream (mic or loopback). No Whisper needed.
            with self._lock:
                self._elapsed_offset += duration
            return
        self._process_chunk(path, duration)

    def _process_chunk(self, path: Path, duration: float) -> None:
        if self._whisper is None:
            raise RuntimeError("Models not loaded. Call load_models() first.")

        # Transcribe
        segments_iter, info = self._whisper.transcribe(
            str(path),
            beam_size=5,
            language=self.language,
            vad_filter=True,
        )
        whisper_segs = list(segments_iter)

        # Diarize (skipped when default_speaker is set)
        speaker_map: dict[tuple[float, float], str] = {}
        if self.default_speaker is None and self._diarizer is not None:
            speaker_map = self._diarize(path, whisper_segs)

        with self._lock:
            for seg in whisper_segs:
                abs_start = self._elapsed_offset + seg.start
                abs_end = self._elapsed_offset + seg.end
                speaker = self.default_speaker if self.default_speaker is not None else speaker_map.get((seg.start, seg.end))
                ts = TranscriptSegment(start=abs_start, end=abs_end, text=seg.text, speaker=speaker)
                self._segments.append(ts)
                if self.on_segment:
                    self.on_segment(ts)

            self._elapsed_offset += duration

    def _diarize(self, path: Path, whisper_segs: list) -> dict[tuple[float, float], str]:
        """Run pyannote diarization; return map of (start, end) → global speaker label."""
        result: dict[tuple[float, float], str] = {}

        try:
            diarization = self._diarizer(str(path))
        except Exception as e:
            print(f"[transcriber] diarization failed for {path.name}: {e}")
            return result

        turns = [(t.start, t.end, spk) for t, _, spk in diarization.itertracks(yield_label=True)]
        if not turns:
            return result

        # Try to extract per-speaker embeddings for cross-chunk tracking
        local_to_global = self._resolve_speakers(path, diarization)

        for seg in whisper_segs:
            best_local = _find_best_speaker(seg.start, seg.end, turns)
            if best_local is None:
                continue
            result[(seg.start, seg.end)] = local_to_global.get(best_local, best_local)

        return result

    def _resolve_speakers(self, path: Path, diarization) -> dict[str, str]:
        """
        Extract per-speaker embeddings from this chunk and resolve to global labels
        via CrossChunkSpeakerTracker. Falls back to sequential local labels if
        embedding extraction fails.
        """
        if self._embedding_inference is None:
            # No embedding model — fall back to simple sequential labels
            fallback: dict[str, str] = {}
            for i, label in enumerate(sorted(diarization.labels()), 1):
                fallback[label] = f"Speaker {self._speaker_tracker._counter + i}"
            # Advance counter so next chunk doesn't collide
            self._speaker_tracker._counter += len(fallback)
            return fallback

        import numpy as np

        chunk_embeddings: dict[str, np.ndarray] = {}
        for speaker in diarization.labels():
            timeline = diarization.label_timeline(speaker)
            speaker_embs = []
            for segment in timeline:
                if segment.duration < 0.5:
                    continue
                try:
                    emb = self._embedding_inference.crop(str(path), segment)
                    speaker_embs.append(np.array(emb).flatten())
                except Exception:
                    continue
            if speaker_embs:
                chunk_embeddings[speaker] = np.mean(speaker_embs, axis=0)

        if not chunk_embeddings:
            return {lbl: lbl for lbl in diarization.labels()}

        return self._speaker_tracker.resolve(chunk_embeddings)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _fmt_time(seconds: float) -> str:
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"


def _find_best_speaker(
    seg_start: float,
    seg_end: float,
    turns: list[tuple[float, float, str]],
) -> Optional[str]:
    """Return the speaker label with greatest overlap with [seg_start, seg_end]."""
    best_spk = None
    best_overlap = 0.0
    for t_start, t_end, spk in turns:
        overlap = max(0.0, min(seg_end, t_end) - max(seg_start, t_start))
        if overlap > best_overlap:
            best_overlap = overlap
            best_spk = spk
    return best_spk
