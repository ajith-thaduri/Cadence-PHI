"""YAML configuration loading."""
from __future__ import annotations

from functools import lru_cache
from typing import Any

import yaml

from harness.paths import CONFIGS, resolve


def load_yaml(path) -> dict[str, Any]:
    with open(resolve(path), encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache(maxsize=1)
def models_registry() -> dict[str, dict[str, Any]]:
    return {k: v for k, v in load_yaml(CONFIGS / "models.yaml")["models"].items()}


def not_added_models() -> list[dict[str, Any]]:
    return load_yaml(CONFIGS / "models.yaml").get("not_added", [])


@lru_cache(maxsize=1)
def benchmark_config() -> dict[str, Any]:
    return load_yaml(CONFIGS / "benchmarks.yaml")


def model_entry(key: str) -> dict[str, Any]:
    reg = models_registry()
    if key not in reg:
        raise KeyError(f"unknown model {key!r}; known: {', '.join(reg)}")
    return reg[key]


def all_model_keys() -> list[str]:
    return list(models_registry())


def default_model_keys() -> list[str]:
    """Models evaluated by default: everything except provenance-excluded entries."""
    return [k for k, e in models_registry().items() if e.get("evaluation_role") != "excluded"]


def published_evidence() -> dict[str, Any]:
    return load_yaml(CONFIGS / "published_evidence.yaml")
