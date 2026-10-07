"""Hardware detection and device selection."""
from __future__ import annotations

import platform
from typing import Any

import psutil


def detect_device(override: str | None = None) -> str:
    import torch

    if override and override != "auto":
        if override == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("--device cuda requested but CUDA is not available")
        if override == "mps" and not torch.backends.mps.is_available():
            raise RuntimeError("--device mps requested but MPS is not available")
        return override
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def hardware_info() -> dict[str, Any]:
    import torch

    info: dict[str, Any] = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python": platform.python_version(),
        "cpu_count_logical": psutil.cpu_count(logical=True),
        "ram_total_gb": round(psutil.virtual_memory().total / 1e9, 2),
        "torch": torch.__version__,
        "cuda_available": torch.cuda.is_available(),
        "mps_available": torch.backends.mps.is_available(),
    }
    if torch.cuda.is_available():
        info["cuda_device"] = torch.cuda.get_device_name(0)
    if platform.system() == "Darwin":
        try:
            import subprocess
            info["cpu_brand"] = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True, timeout=5
            ).stdout.strip()
        except Exception:  # noqa: BLE001
            pass
    return info


def library_versions() -> dict[str, str]:
    import importlib.metadata as md

    out = {}
    for pkg in ["torch", "transformers", "gliner", "tokenizers", "huggingface_hub", "numpy",
                "pandas", "scikit-learn", "matplotlib", "jinja2", "pydantic", "pyyaml"]:
        try:
            out[pkg] = md.version(pkg)
        except md.PackageNotFoundError:
            out[pkg] = "not installed"
    return out
