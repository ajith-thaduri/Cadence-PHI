"""Adapter construction from the model registry.

One implementation per inference family; each model's behaviour comes from
configs/models.yaml, configs/gliner/*.yaml and configs/label_mapping/*.yaml.
"""
from __future__ import annotations

from harness.adapters.base import BaseModelAdapter
from harness.adapters.gliner_adapter import GLiNERAdapter
from harness.adapters.hf_token_classifier import HFTokenClassificationAdapter
from harness.config import model_entry


class StanfordBaseAdapter(HFTokenClassificationAdapter):
    pass


class StanfordV2Adapter(HFTokenClassificationAdapter):
    pass


class LogicalGLiNERAdapter(GLiNERAdapter):
    pass


class MedNotesGLiNERAdapter(GLiNERAdapter):
    pass


ADAPTERS: dict[str, type[BaseModelAdapter]] = {
    "stanford_base": StanfordBaseAdapter,
    "stanford_v2": StanfordV2Adapter,
    "logical_gliner": LogicalGLiNERAdapter,
    "mednotes_gliner": MedNotesGLiNERAdapter,
}
GENERIC = {"hf_token_classification": HFTokenClassificationAdapter, "gliner": GLiNERAdapter}


def build_adapter(key: str, device: str, long_text_strategy: str = "author") -> BaseModelAdapter:
    cls = ADAPTERS.get(key) or GENERIC[model_entry(key)["adapter"]]
    return cls(key, device, long_text_strategy)
