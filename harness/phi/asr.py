"""ASR as an interchangeable stage in front of the shield.

  raw text            -> shield
  ASR transcript      -> shield               (pre-computed transcripts, e.g. cadence_asr)
  audio -> ASRAdapter -> shield               (local engine only; nothing leaves the machine)

Any engine implementing ASRAdapter.transcribe() can be swapped in. The default is local
Whisper-small at a pinned revision (the same engine used to build the ASR datasets).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional


WHISPER_ID, WHISPER_REV = "openai/whisper-small", "973afd24965f72e36ca33b3055d56a652f456b4d"   # pinned


class Transcriber:
    """Local Whisper pipeline at a pinned revision (no network when the model is cached; OFFLINE_MODE)."""

    def __init__(self, device: str = "cpu") -> None:
        import torch
        from transformers import pipeline
        self.pipe = pipeline("automatic-speech-recognition", model=WHISPER_ID, revision=WHISPER_REV,
                             device=device, torch_dtype=torch.float32)

    def __call__(self, audio, sr: int, language: Optional[str]) -> str:
        kw = {"generate_kwargs": {"task": "transcribe", **({"language": language} if language else {})}}
        if len(audio) / sr > 29:   # long single-speaker turns need Whisper long-form decoding
            kw["return_timestamps"] = True
        return self.pipe({"raw": audio, "sampling_rate": sr}, **kw)["text"].strip()


class ASRAdapter(ABC):
    name = "asr"

    @abstractmethod
    def transcribe(self, audio_path: Path, language: Optional[str] = None) -> str: ...


class WhisperASR(ASRAdapter):
    name = "whisper-small"

    def __init__(self, device: str = "cpu") -> None:
        self.t = Transcriber(device)

    def transcribe(self, audio_path: Path, language: Optional[str] = None) -> str:
        import numpy as np
        from scipy import signal
        from scipy.io import wavfile
        sr, x = wavfile.read(str(audio_path))     # local WAV only
        x = x.astype(np.float32) / (32768.0 if x.dtype == np.int16 else 1.0)
        if x.ndim > 1:
            x = x.mean(axis=1)
        if sr != 16000:
            x = signal.resample_poly(x, 16000, sr).astype(np.float32)
        return self.t(x, 16000, {"en": "english", "en-US": "english"}.get(language or "", None))
