# whisper_utils.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import os
import time
import shutil
import tempfile
from typing import Optional, Dict, Any

import numpy as np

# -----------------------------
# Backend detection (prefer whisper-cpp-python)
# -----------------------------
_BACKEND = None          # "whisper_cpp_python" | "whispercpp" | None
_Whisper = None          # bound class/type
_BACKEND_NOTE = ""

# Prefer the binding you installed: whisper-cpp-python
try:
    # pip install whisper-cpp-python ; from whisper_cpp_python import Whisper
    from whisper_cpp_python import Whisper as _Whisper  # type: ignore
    _BACKEND = "whisper_cpp_python"
    _BACKEND_NOTE = "using whisper-cpp-python backend"
except Exception:
    try:
        # aarnphm/whispercpp: pip install whispercpp ; from whispercpp import Whisper
        from whispercpp import Whisper as _Whisper  # type: ignore
        _BACKEND = "whispercpp"
        _BACKEND_NOTE = "using whispercpp (Pybind11) backend"
    except Exception:
        _BACKEND = None
        _Whisper = None
        _BACKEND_NOTE = "no whisper.cpp backend available"

# -----------------------------
# Transcriber (singleton)
# -----------------------------

class _Transcriber:
    """
    Async-friendly wrapper around whisper.cpp Python bindings.
    - Resamples Discord PCM (48 kHz stereo s16) to 16 kHz mono float32.
    - Runs transcription in a thread executor to avoid blocking the event loop.
    - Tracks simple performance stats for /transcription_stats.
    """
    def __init__(self, model_size: Optional[str] = None):
        self.model_size = (model_size or os.getenv("WHISPER_MODEL") or "tiny.en").strip()
        self.backend = _BACKEND
        self.backend_note = _BACKEND_NOTE
        self._model = None
        self._model_loaded_path: Optional[str] = None
        self._lock = asyncio.Lock()

        # perf stats
        self.transcription_count = 0
        self._total_time = 0.0
        self._avg_time = 0.0

    # -------------------------
    # Public API (used by bot.py)
    # -------------------------
    async def transcribe_audio(self, pcm_bytes: bytes, *, language: Optional[str] = "en",
                               translate: bool = False) -> str:
        """
        Accept raw PCM s16le 48kHz stereo from py-cord WaveSink, convert to 16k mono float32,
        and run whisper.cpp transcription.
        """
        if not pcm_bytes:
            return ""

        # Ensure model is loaded lazily
        await self._ensure_loaded()

        # If no backend/model, return empty so bot falls back to SpeechRecognition
        if self._model is None:
            return ""

        try:
            audio = await self._to_float32_mono_16k(pcm_bytes)
        except Exception:
            # If resampling fails (ffmpeg missing), safe fail
            return ""

        loop = asyncio.get_running_loop()
        started = time.perf_counter()
        try:
            text = await loop.run_in_executor(
                None,
                self._blocking_transcribe,
                audio,
                language,
                translate,
            )
        finally:
            elapsed = time.perf_counter() - started
            self.transcription_count += 1
            self._total_time += elapsed
            self._avg_time = self._total_time / max(1, self.transcription_count)

        return (text or "").strip()

    def get_performance_stats(self) -> Dict[str, Any]:
        return {
            "backend": self.backend or "none",
            "backend_note": self.backend_note,
            "whisper_available": bool(self.backend and self._model is not None),
            "model_loaded": self._model is not None,
            "model_size": self.model_size,
            "transcription_count": self.transcription_count,
            "average_time": round(self._avg_time, 3),
        }

    # -------------------------
    # Internal
    # -------------------------
    async def _ensure_loaded(self):
        if self._model is not None:
            return
        if self.backend is None or _Whisper is None:
            return
        async with self._lock:
            if self._model is not None:
                return

            if self.backend == "whispercpp":
                # aarnphm/whispercpp supports from_pretrained("tiny.en"|"base.en"|...)
                self._model = _Whisper.from_pretrained(self.model_size)  # downloads/caches if needed
                self._model_loaded_path = f"pretrained://{self.model_size}"

            elif self.backend == "whisper_cpp_python":
                # This backend requires a local ggml model path
                model_path = os.getenv("WHISPER_CPP_MODEL")
                if not model_path or not os.path.exists(model_path):
                    # try a few common fallbacks relative to cwd
                    guesses = [
                        f"./models/ggml-{self.model_size.replace('.','-')}.bin",
                        f"./models/ggml-{self.model_size}.bin",
                        "./models/ggml-tiny.en.bin",
                        "./models/ggml-tiny.bin",
                    ]
                    for g in guesses:
                        if os.path.exists(g):
                            model_path = g
                            break
                if not model_path or not os.path.exists(model_path):
                    # Can't load anything
                    self._model = None
                    self._model_loaded_path = None
                    return

                self._model = _Whisper(model_path)  # whisper-cpp-python expects a file path
                self._model_loaded_path = model_path

    def _blocking_transcribe(self, audio_f32_mono_16k: np.ndarray,
                             language: Optional[str],
                             translate: bool) -> str:
        if self._model is None:
            return ""

        # Backend-specific calls
        if self.backend == "whispercpp":
            # Configure params each call where available
            try:
                if language:
                    self._model.params.with_language(language)
                self._model.params.with_translate(bool(translate))
            except Exception:
                pass

            try:
                result = self._model.transcribe(audio_f32_mono_16k)
            except Exception:
                return ""
            return self._extract_text(result)

        elif self.backend == "whisper_cpp_python":
            # This backend prefers file paths; write a temp WAV
            try:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
                    self._write_wav_16k_mono_s16(tmp.name, audio_f32_mono_16k)
                    result = self._model.transcribe(tmp.name)  # type: ignore[attr-defined]
            except Exception:
                return ""
            return self._extract_text(result)

        return ""

    # Result normalization across backends
    @staticmethod
    def _extract_text(result: Any) -> str:
        if isinstance(result, str):
            return result
        # common dict/list shapes
        if isinstance(result, dict):
            if "text" in result:
                return str(result["text"])
            if "segments" in result and isinstance(result["segments"], list):
                return "".join(seg.get("text", "") for seg in result["segments"])
        if isinstance(result, (list, tuple)):
            parts = []
            for seg in result:
                if isinstance(seg, dict) and "text" in seg:
                    parts.append(seg["text"])
            if parts:
                return "".join(parts)
        # last resort
        try:
            return str(getattr(result, "text", "")) or ""
        except Exception:
            return ""

    # Convert raw PCM s16le 48kHz stereo to float32 mono @ 16kHz
    async def _to_float32_mono_16k(self, pcm_bytes_48k_stereo: bytes) -> np.ndarray:
        """Uses ffmpeg via subprocess for high-quality resampling without extra Python deps."""
        if not shutil.which("ffmpeg"):
            raise RuntimeError("ffmpeg not installed")

        # Feed raw PCM in; receive raw PCM out (16k mono s16le)
        # Input:  s16le, 48k, 2ch  (Discord voice receive format)
        # Output: s16le, 16k, 1ch
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-nostdin",
            "-hide_banner", "-loglevel", "error",
            "-f", "s16le", "-ar", "48000", "-ac", "2", "-i", "pipe:0",
            "-f", "s16le", "-ar", "16000", "-ac", "1", "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate(input=pcm_bytes_48k_stereo)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg resample failed: {err.decode(errors='ignore')}")
        # s16 -> f32 mono in [-1,1]
        audio_i16 = np.frombuffer(out, dtype=np.int16)
        audio_f32 = (audio_i16.astype(np.float32) / 32768.0).flatten()
        return audio_f32

    @staticmethod
    def _write_wav_16k_mono_s16(path: str, audio_f32_mono_16k: np.ndarray):
        """Write a small WAV if a backend insists on files (whisper-cpp-python)."""
        import wave
        # Clip to int16
        pcm = np.clip(audio_f32_mono_16k * 32768.0, -32768, 32767).astype(np.int16).tobytes()
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(pcm)


# -----------------------------
# Module-level helpers to match your imports in bot.py
# -----------------------------

_GLOBAL: Optional[_Transcriber] = None

async def get_transcriber(model_size: Optional[str] = None) -> _Transcriber:
    global _GLOBAL
    if _GLOBAL is None:
        _GLOBAL = _Transcriber(model_size)
    return _GLOBAL

async def transcribe_audio(pcm_bytes: bytes, model_size: Optional[str] = None,
                           *, language: Optional[str] = "en",
                           translate: bool = False) -> str:
    tr = await get_transcriber(model_size)
    return await tr.transcribe_audio(pcm_bytes, language=language, translate=translate)
