"""Shared CLI helpers."""
from __future__ import annotations

import logging
import sys
import warnings


def setup_logging(debug: bool = False) -> None:
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S", stream=sys.stderr)
    # Third-party loggers can echo inputs at DEBUG; keep them quiet regardless of --debug.
    for noisy in ("transformers", "urllib3", "filelock", "huggingface_hub", "matplotlib", "PIL", "httpx"):
        logging.getLogger(noisy).setLevel(logging.ERROR)
    try:
        from transformers.utils import logging as tlog
        tlog.set_verbosity_error()
        tlog.disable_progress_bar()
    except Exception:  # noqa: BLE001
        pass
    warnings.filterwarnings("ignore", category=FutureWarning)
    warnings.filterwarnings("ignore", category=UserWarning)
