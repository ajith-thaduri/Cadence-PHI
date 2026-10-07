"""Offline-mode and environment handling."""
from __future__ import annotations

import os

_TRUE = {"1", "true", "yes", "on"}


def offline_mode_enabled() -> bool:
    """OFFLINE_MODE defaults to true. Only `harness.download` turns it off."""
    return os.environ.get("OFFLINE_MODE", "true").strip().lower() in _TRUE


def apply_offline_mode() -> None:
    """Force Hugging Face libraries to use the local cache only when OFFLINE_MODE is on."""
    if offline_mode_enabled():
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["HF_DATASETS_OFFLINE"] = "1"
    # Never send telemetry.
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
