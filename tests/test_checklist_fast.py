"""Behavioural CheckList (T1) as a regression test - fast tier: deterministic, conversational and registry
recognizers only, no model weights. Gate G5: every MFT / INV / DIR case passes for every enabled entity."""
from __future__ import annotations

import pytest

from harness.phi.config import HarnessConfig
from harness.phi.engine import PHIEngine
from harness_eval import checklist


@pytest.fixture(scope="module")
def fast_rows():
    cfg = HarnessConfig.load(env="test")
    cfg.only_models([])
    eng = PHIEngine(cfg, device="cpu", self_test=False)
    return cfg, checklist.run_cases(eng, checklist.generate({"fast"}))


def _fmt(rows):
    return "\n".join(f"{r['test_type']} {r['entity']}: expected {r['expected']}, got {r['got']}" for r in rows[:20])


def test_g5_mft_all_enabled_entities(fast_rows):
    cfg, rows = fast_rows
    s = checklist.summarize(rows, cfg)
    assert s["gate_G5_mft_100pct"], _fmt([r for r in rows if r["test_type"] == "MFT" and not r["pass"]])


def test_invariance_and_directional(fast_rows):
    _, rows = fast_rows
    bad = [r for r in rows if r["test_type"].startswith(("INV", "DIR")) and not r["pass"]]
    assert not bad, _fmt(bad)


def test_negatives_never_redacted(fast_rows):
    _, rows = fast_rows
    neg = [r for r in rows if r["test_type"] == "DIR:negative"]
    assert neg and all(r["pass"] for r in neg), _fmt([r for r in neg if not r["pass"]])


def test_chunk_boundary_pad_is_whole_sentences():
    text, span = "MRN 4471-2290", (4, 13)
    t, (s, e) = checklist._apply({"op": "pad_before", "arg": 2400}, text, span)
    assert len(t) - len(text) >= 2400 and t[s:e] == "4471-2290"
    assert t[: len(t) - len(text)].endswith(". ")          # the pad never glues onto the keyword
