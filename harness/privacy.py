"""Guards that keep document text out of logs and artifacts unless it is synthetic."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

PUBLIC_DATA_WARNING = (
    "WARNING: you are evaluating NON-SYNTHETIC clinical text. It must be a dataset you are "
    "licensed to use (e.g. an n2c2/i2b2 DUA). Inference stays local; document text is "
    "redacted from logs, prediction files and false-negative reports."
)


class TextPolicy:
    """Decides whether raw text may be written to logs / result files."""

    def __init__(self, is_synthetic: bool, show_public_text: bool = False) -> None:
        self.is_synthetic = is_synthetic
        self.allow_text = is_synthetic or show_public_text

    def text(self, s: str | None) -> str | None:
        if s is None:
            return None
        return s if self.allow_text else f"[REDACTED {len(s)} chars]"
