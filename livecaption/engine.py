"""Transcription engine: model loading and the real-time worker.

Pipeline (one capture source, one worker thread):

    capture callback -> bounded queue -> worker: backlog limit -> mono float32
    -> chunk buffer with overlap -> RMS silence gate -> resample to 16 kHz
    -> language choice -> faster-whisper (with its Silero VAD) -> overlap
    removal -> hallucination filter -> caption event

The transcribe() options, overlap removal and hallucination filter are carried
over from live_caption_v2_speed_optimized.py.
"""

from __future__ import annotations

import gc
import logging
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from . import hardware
from .audio import BLOCK_FRAMES, AudioCapture, Resampler, pcm16_to_mono_float32

log = logging.getLogger(__name__)

SUPPORTED_LANGUAGES = ("en", "es")
LANGUAGE_NAMES = {"en": "English", "es": "Spanish", "auto": "Auto (English/Spanish)"}
GPU_COMPUTE_TYPES = ("float16", "int8_float16", "int8")
CPU_COMPUTE_TYPES = ("int8", "int8_float32", "float32")
WHISPER_SAMPLES_WARMUP = 16000      # 1 s of silence for the GPU check

Event = Callable[[str, object], None]


# ---------------------------------------------------------------------------
# Text helpers (from the original scripts)
# ---------------------------------------------------------------------------

def normalize_word(word: str) -> str:
    return re.sub(r"(^\W+|\W+$)", "", word).lower()


def remove_overlap(previous_words: list[str], current_text: str, max_words: int = 24) -> str:
    """Drop words repeated from the previous chunk because of the audio overlap."""
    words = current_text.split()
    if not previous_words or not words:
        return current_text.strip()
    prev = [normalize_word(w) for w in previous_words]
    curr = [normalize_word(w) for w in words]
    for count in range(min(max_words, len(prev), len(curr)), 1, -1):
        if prev[-count:] == curr[:count]:
            return " ".join(words[count:]).strip()
    return current_text.strip()


def suspicious_text(text: str) -> bool:
    """Very conservative hallucination filter."""
    if not text:
        return True
    if text.lower().count(".js") >= 4:
        return True
    words = [w for w in (normalize_word(w) for w in text.split()) if w]
    return len(words) >= 8 and len(set(words)) / len(words) < 0.20


# ---------------------------------------------------------------------------
# Language selection
# ---------------------------------------------------------------------------

def pick_language(all_probs, min_mass: float = 0.5) -> tuple[str | None, float]:
    """Choose English or Spanish from Whisper's language probabilities.

    Returns (None, p) when the speech is most likely another language: the top
    language is neither English nor Spanish and English+Spanish together are
    below min_mass. Never forces other languages into en/es.
    """
    probs = dict(all_probs)
    p_en, p_es = probs.get("en", 0.0), probs.get("es", 0.0)
    top = max(probs, key=probs.get) if probs else None
    best = "en" if p_en >= p_es else "es"
    if top in SUPPORTED_LANGUAGES:
        return top, probs[top]
    if p_en + p_es >= min_mass:
        return best, max(p_en, p_es)
    return None, max(p_en, p_es)


class LanguageTracker:
    """Caches the detected language so detection is not run on every chunk.

    Detection runs on the first speech, every `redetect_seconds` after that,
    after `silence_reset` seconds without speech, or when forced. A different
    language replaces the current one only with confidence >= switch_conf.
    """

    def __init__(self, mode: str, redetect_seconds: float = 30.0,
                 silence_reset: float = 10.0, switch_conf: float = 0.6):
        self.mode = mode
        self.redetect_seconds = redetect_seconds
        self.silence_reset = silence_reset
        self.switch_conf = switch_conf
        self.current: str | None = None if mode == "auto" else mode
        self.last_detect = -1e9
        self.last_speech = -1e9

    def needs_detection(self, now: float) -> bool:
        if self.mode != "auto":
            return False
        return (self.current is None
                or now - self.last_detect >= self.redetect_seconds
                or now - self.last_speech >= self.silence_reset)

    def force(self) -> None:
        self.last_detect = -1e9

    def update(self, lang: str | None, conf: float, now: float) -> str | None:
        """Record a detection and return the language to use (None = skip chunk)."""
        self.last_detect = now
        if lang is None:   # another language: skip this chunk, keep the cached one
            return None
        if self.current is None or lang == self.current or conf >= self.switch_conf:
            self.current = lang
        return self.current


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------

class GpuUnavailable(RuntimeError):
    """GPU was requested explicitly but cannot be used."""


@dataclass
class LoadedModel:
    model: object
    model_id: str
    path: Path
    device: str
    compute_type: str
    load_seconds: float
    note: str = ""


def load_model(model_id: str, path: Path, device_pref: str = "auto",
               compute_pref: str = "auto", factory=None) -> LoadedModel:
    """Load a model from a local folder. Never downloads anything.

    device_pref "auto" tries CUDA and falls back to CPU with a note;
    "cuda" raises GpuUnavailable instead of falling back silently.
    The GPU is checked with a real load plus a tiny encoder pass, because a
    visible CUDA device does not guarantee cuBLAS/cuDNN are loadable.
    """
    if factory is None:
        from faster_whisper import WhisperModel as factory
    start = time.monotonic()
    note = ""
    if device_pref in ("auto", "cuda"):
        ok, msg, types = hardware.cuda_runtime_check()
        if ok:
            ct = compute_pref if compute_pref in GPU_COMPUTE_TYPES else (
                "float16" if "float16" in types else "int8")
            model = None
            try:
                model = factory(str(path), device="cuda", device_index=0,
                                compute_type=ct, num_workers=1)
                model.detect_language(np.zeros(WHISPER_SAMPLES_WARMUP, dtype=np.float32))
                return LoadedModel(model, model_id, Path(path), "cuda", ct,
                                   time.monotonic() - start)
            except Exception as exc:
                del model
                gc.collect()
                msg = f"GPU load failed: {exc}"
        if device_pref == "cuda":
            raise GpuUnavailable(msg)
        note = f"{msg} Using CPU."
    ct = compute_pref if compute_pref in CPU_COMPUTE_TYPES else "int8"
    model = factory(str(path), device="cpu", compute_type=ct,
                    cpu_threads=hardware.cpu_threads(), num_workers=1)
    return LoadedModel(model, model_id, Path(path), "cpu", ct, time.monotonic() - start, note)


# ---------------------------------------------------------------------------
# Real-time worker
# ---------------------------------------------------------------------------

@dataclass
class StreamOptions:
    language: str = "auto"
    redetect_seconds: float = 30.0
    chunk_seconds: float = 3.0
    overlap_seconds: float = 0.5
    silence_threshold: float = 0.0012
    max_backlog_seconds: float = 6.0
    beam_size: int = 1
    audio_source: str = "loopback"
    audio_device: str = ""
    preferred_device_name: str = ""


@dataclass
class Metrics:
    chunks: int = 0
    skipped_silent: int = 0
    skipped_other_language: int = 0
    skipped_suspicious: int = 0
    last_inference_ms: float = 0.0
    avg_inference_ms: float = 0.0
    realtime_factor: float = 0.0       # inference time / chunk audio length
    last_delay_ms: float = 0.0         # end of chunk audio captured -> caption ready
    backlog_seconds: float = 0.0
    dropped_seconds: float = 0.0       # audio discarded to keep latency bounded
    language: str | None = None
    extra: dict = field(default_factory=dict)


class Transcriber:
    """Runs capture + worker threads for one loaded model."""

    def __init__(self, loaded: LoadedModel, options: StreamOptions, emit: Event,
                 capture_factory=AudioCapture):
        self.loaded = loaded
        self.opt = options
        self.emit = emit
        self.metrics = Metrics()
        self.paused = threading.Event()
        self._stop = threading.Event()
        self._queue: queue.Queue = queue.Queue(maxsize=4096)
        self._capture = capture_factory(self._queue, options.audio_source, options.audio_device,
                                        options.preferred_device_name, self._on_audio_status)
        self._worker: threading.Thread | None = None
        self.language = LanguageTracker(options.language, options.redetect_seconds)
        self._recent_words: list[str] = []
        self._resample: Resampler | None = None
        self.error: Exception | None = None

    # -- control --------------------------------------------------------------

    def start(self) -> None:
        self._stop.clear()
        self._worker = threading.Thread(target=self._run, name="Transcriber", daemon=True)
        self._worker.start()
        self._capture.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._capture.stop(timeout)
        if self._worker is not None:
            self._worker.join(timeout)
            if self._worker.is_alive():
                log.warning("Transcriber worker did not stop within %.1fs", timeout)
            self._worker = None

    @property
    def running(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def redetect_language(self) -> None:
        self.language.force()

    def _on_audio_status(self, text: str, is_error: bool) -> None:
        self.emit("audio", (text, is_error))

    # -- worker -----------------------------------------------------------------

    def _run(self) -> None:
        try:
            self._loop()
        except Exception as exc:
            log.exception("Transcription worker failed")
            self.error = exc
            self.emit("error", f"Transcription stopped: {exc}")
        finally:
            self.emit("stopped", self)

    def _drop_backlog(self, rate: int) -> None:
        """Bound latency: when queued audio exceeds the limit, drop the oldest
        audio down to half the limit. Speech in the dropped part is lost; the
        dropped total is shown in the metrics."""
        block_s = BLOCK_FRAMES / rate
        backlog = self._queue.qsize() * block_s
        self.metrics.backlog_seconds = backlog
        if backlog <= self.opt.max_backlog_seconds:
            return
        target = self.opt.max_backlog_seconds / 2
        dropped = 0
        while self._queue.qsize() * block_s > target:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
            dropped += 1
        self.metrics.dropped_seconds += dropped * block_s
        self._recent_words.clear()
        self.emit("status", f"Falling behind: skipped {dropped * block_s:.1f}s of audio")

    def _loop(self) -> None:
        rate = channels = None
        buf = np.empty(0, dtype=np.float32)
        fill = 0
        chunk_n = advance = 0
        last_block_time = 0.0
        while not self._stop.is_set():
            try:
                r, ch, data, captured_at = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if (r, ch) != (rate, channels):          # first block or device changed
                rate, channels = r, ch
                chunk_n = int(rate * self.opt.chunk_seconds)
                advance = chunk_n - int(rate * self.opt.overlap_seconds)
                buf = np.empty(chunk_n + rate, dtype=np.float32)
                fill = 0
                self._resample = Resampler(rate)
            self._drop_backlog(rate)
            if self.paused.is_set():
                fill = 0
                continue
            block = pcm16_to_mono_float32(data, channels)
            if fill + len(block) > len(buf):
                buf = np.concatenate((buf[:fill], np.empty(len(block) + rate, np.float32)))
            buf[fill:fill + len(block)] = block
            fill += len(block)
            last_block_time = captured_at
            if fill < chunk_n:
                continue
            chunk = buf[:chunk_n].copy()
            keep = fill - advance
            buf[:keep] = buf[advance:fill]
            fill = keep
            self._process(chunk, last_block_time)

    def _process(self, chunk: np.ndarray, captured_at: float) -> None:
        m = self.metrics
        rms = float(np.sqrt(np.mean(chunk * chunk)))
        if rms < self.opt.silence_threshold:
            m.skipped_silent += 1
            return
        now = time.monotonic()
        audio = self._resample(chunk)
        model = self.loaded.model
        lang = self.language.current
        if self.language.needs_detection(now):
            _, _, all_probs = model.detect_language(audio)
            lang = self.language.update(*pick_language(all_probs), now)
            if lang is None:
                m.skipped_other_language += 1
                self.emit("language", None)
                self.language.last_speech = now
                return
            self.emit("language", lang)
        m.language = lang
        self.language.last_speech = now
        t0 = time.monotonic()
        segments, _ = model.transcribe(
            audio, language=lang, beam_size=self.opt.beam_size, temperature=0.0,
            vad_filter=True, vad_parameters=dict(min_silence_duration_ms=150),
            condition_on_previous_text=False, initial_prompt=None,
            no_speech_threshold=0.55, compression_ratio_threshold=2.4)
        text = " ".join(s.text.strip() for s in segments if s.text.strip()).strip()
        infer = time.monotonic() - t0
        m.chunks += 1
        m.last_inference_ms = infer * 1000
        m.avg_inference_ms += (m.last_inference_ms - m.avg_inference_ms) / m.chunks
        m.realtime_factor = infer / self.opt.chunk_seconds
        if text:
            text = remove_overlap(self._recent_words, text)
            if text and suspicious_text(text):
                m.skipped_suspicious += 1
                log.info("Skipped suspicious transcription: %s", text)
            elif text:
                self._recent_words = (self._recent_words + text.split())[-80:]
                self.emit("caption", {"text": text, "language": lang, "time": time.time()})
        m.last_delay_ms = (time.monotonic() - captured_at) * 1000
        self.emit("metrics", m)
