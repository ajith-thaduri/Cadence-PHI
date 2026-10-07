"""Model tier (pytest -m model): needs the pinned local OBI weights. Runs fully offline - the Hugging Face
hub is forced offline so a missing snapshot fails instead of downloading, and no inference leaves the machine."""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.model


@pytest.fixture(scope="module", autouse=True)
def offline():
    old = {k: os.environ.get(k) for k in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")}
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    yield
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_checklist_model_tier_and_name_origin_invariance():
    from harness.phi.config import HarnessConfig
    from harness.phi.engine import PHIEngine
    from harness_eval import checklist
    cfg = HarnessConfig.load(env="test")
    eng = PHIEngine(cfg, device="cpu", self_test=True)          # startup self-test must pass with real weights
    rows = checklist.run_cases(eng, checklist.generate({"model"}))
    bad = [r for r in rows if not r["pass"]]
    assert not bad, "\n".join(f"{r['test_type']} {r['entity']}: {r['got']}" for r in bad[:20])
    assert any(r["test_type"] == "INV:name_origin" for r in rows)
