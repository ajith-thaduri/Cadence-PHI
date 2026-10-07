"""PHI Shield pipeline stages evaluated by the benchmark.

  MODEL_ONLY                       raw model predictions (no post-processing)
  DETERMINISTIC_ONLY               rules + spoken-digit normalisation, reconciled
  DETERMINISTIC_REGISTRY           rules + registry matching (no ML)
  MODEL_DETERMINISTIC              model + rules, reconciled
  MODEL_DETERMINISTIC_REGISTRY     model + rules + registry matching, reconciled (full shield)
"""
from __future__ import annotations

from harness.schema import Prediction, Sample
from harness.shield import registry, rules
from harness.shield.reconcile import reconcile

STAGES = ["MODEL_ONLY", "DETERMINISTIC_ONLY", "DETERMINISTIC_REGISTRY", "MODEL_DETERMINISTIC",
          "MODEL_DETERMINISTIC_REGISTRY"]
MODEL_FREE = {"DETERMINISTIC_ONLY", "DETERMINISTIC_REGISTRY"}


class ShieldComponents:
    """Caches model-independent detector output per sample."""

    def __init__(self) -> None:
        self._rules: dict[str, list[Prediction]] = {}
        self._reg: dict[str, list[Prediction]] = {}

    def rules(self, s: Sample) -> list[Prediction]:
        if s.id not in self._rules:
            self._rules[s.id] = rules.detect(s.text)
        return self._rules[s.id]

    def registry(self, s: Sample) -> list[Prediction]:
        if s.id not in self._reg:
            self._reg[s.id] = registry.match(s.text, s.metadata.get("registry"))
        return self._reg[s.id]

    def stage(self, name: str, s: Sample, model_preds: list[Prediction] | None) -> list[Prediction]:
        mp = model_preds or []
        if name == "MODEL_ONLY":
            return mp
        if name == "DETERMINISTIC_ONLY":
            return reconcile(s.text, self.rules(s))
        if name == "DETERMINISTIC_REGISTRY":
            return reconcile(s.text, self.rules(s) + self.registry(s))
        if name == "MODEL_DETERMINISTIC":
            return reconcile(s.text, mp + self.rules(s))
        if name == "MODEL_DETERMINISTIC_REGISTRY":
            return reconcile(s.text, mp + self.rules(s) + self.registry(s))
        raise KeyError(name)
