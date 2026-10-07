"""Prompt suite (T2) regression - fast tier (no model weights) against the reviewed baseline_fast.json.
A protected entity that becomes unprotected, a new false positive or must-not-redact violation, or a
residual increase fails the test. The suite is a regression net, never a tuning set: when this fails,
fix the general behaviour (or re-baseline through review), never special-case a prompt."""
from __future__ import annotations

from harness.paths import ROOT
from harness.phi.config import HarnessConfig
from harness_eval import manifests, suite

SUITE = ROOT / "eval_suites" / "english_v1"


def test_suite_manifest_signed():
    manifests.verify("english_v1_suite")


def test_fast_tier_no_regressions(tmp_path):
    cfg = HarnessConfig.load(env="test")
    cfg.only_models([])
    summ = suite.run_suite(SUITE, cfg, "cpu", tmp_path)
    regs, _ = suite.regress(SUITE, tmp_path, name="baseline_fast.json")
    assert not regs, "\n".join(regs[:30])
    assert summ["prompts"] == 215
