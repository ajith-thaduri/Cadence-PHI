"""P2 evaluation core: statistics, stage attribution, independent redaction validation, manifests,
contamination, ledger and scorer definitions."""
from __future__ import annotations

import json

import pytest

from harness.phi.catalog import Catalog
from harness.phi.config import HarnessConfig
from harness.phi.contracts import (DetectionEvidence, DetectionRequest, GoldDocument, GoldEntity, Operator, RedactionRequest,
                                   RedactionResponse, Replacement)
from harness.phi.engine import PHIEngine
from harness.phi.redaction import redact
from harness_eval import attribution, contamination, ledger, manifests, stats
from harness_eval.redaction_check import check
from harness_eval.scoring import Bundle, core_chars, score
from test_phi_harness import SPANS, TEXT, FakeAdapter, request, store


# ---------------------------------------------------------------------------- statistics
def test_wilson_and_rule_of_three():
    lo, hi = stats.wilson(95, 100)
    assert 0.88 < lo < 0.90 and 0.97 < hi < 0.99
    assert stats.wilson(0, 0) == (None, None)
    assert stats.rule_of_three(300) == pytest.approx(0.01)                 # 0 misses in 300 -> miss rate < 1%


def test_cluster_bootstrap_is_deterministic_and_brackets_mean():
    units = [0.1, 0.2, 0.3, 0.4, 0.5] * 10
    f = lambda xs: sum(xs) / len(xs)
    a, b = stats.cluster_bootstrap(units, f, 500, 7), stats.cluster_bootstrap(units, f, 500, 7)
    assert a == b and a[0] < 0.3 < a[1]


def test_paired_randomization():
    same = stats.paired_randomization([0.1, 0.2, 0.3] * 10, [0.1, 0.2, 0.3] * 10, 2000)
    diff = stats.paired_randomization([0.1] * 30, [0.5] * 30, 2000)
    assert same > 0.5 and diff < 0.01


# ---------------------------------------------------------------------------- stage decision tree
def ev(rid="model_obi_roberta", acc=False, reason=None, score_=0.4, view="original"):
    return DetectionEvidence(evidence_id=f"ev_{rid}_{reason}", entity_type="NAME", original_start=0, original_end=5,
                             raw_score=score_, score_type="model_confidence" if rid.startswith("model_") else "regex_match",
                             recognizer_id=rid, recognizer_version="1", source="model" if rid.startswith("model_") else "rule",
                             text_view_id=view, threshold_used=0.5, accepted_as_candidate=acc, rejection_reason=reason)


@pytest.mark.parametrize("left_det,left_out,overlap,near,expected", [
    (0, 0, True, [ev(acc=True), ev("registry_member", acc=True)], "detected_by_multiple_sources"),
    (0, 0, True, [ev(acc=True)], "detected_by_ml"),
    (0, 3, True, [ev(acc=True)], "redaction_failure"),
    (2, 2, True, [ev(acc=True)], "partial_span"),
    (5, 5, False, [ev(acc=True)], "reconciliation_failure"),
    (5, 5, False, [ev(reason="entity_disabled")], "policy_rejected"),
    (5, 5, False, [ev(reason="approximate_mapping", view="normalized")], "normalization_failure"),
    (5, 5, False, [ev(reason="below_threshold"), ev("model_stanford_v2", reason="model_disabled")], "below_threshold"),
    (5, 5, False, [ev("model_stanford_v2", reason="model_disabled")], "disabled_only"),
    (5, 5, False, [], "no_candidate"),
])
def test_stage_decision_tree(left_det, left_out, overlap, near, expected):
    c = attribution.classify(0, 5, left_det, left_out, overlap, near)
    assert c["outcome"] == expected
    assert c["stage"] == attribution.STAGE.get(expected)


def test_sole_catch_and_groups():
    c = attribution.classify(0, 5, 0, 0, True, [ev("registry_member", acc=True)])
    assert c["sole_catch"] == "REGISTRY" and c["sources"] == ["REGISTRY"]
    assert attribution.source_group("conv_spoken_id") == "CONVERSATIONAL"
    assert attribution.source_group("model_obi_roberta") == "MODEL:obi_roberta"
    assert attribution.source_group("pattern_ssn") == "DETERMINISTIC"


# ---------------------------------------------------------------------------- redaction validation
def test_redaction_check_catches_faulty_output_and_mnr():
    text = "Call Maria at 555-013-2244 about the 500 mg dose."
    rr = RedactionResponse(request_id="r", sanitized_text="Call [NAME_1] at 555-013-2244 about the [X] dose.", operator="TOKENIZE",
                           replacements=[Replacement(detection_id="d1", entity_type="NAME", original_start=5, original_end=10,
                                                     output_start=5, output_end=13, operator="TOKENIZE", replacement="[NAME_1]"),
                                         Replacement(detection_id="d2", entity_type="X", original_start=37, original_end=43,
                                                     output_start=37, output_end=40, operator="TOKENIZE", replacement="[X]")],
                           original_sha256="x")
    c = check(text, rr, [("a1", 5, 10), ("a2", 14, 26)], [(37, 43, "500 mg")])
    assert "a2" in c.verbatim_leaks and "a1" not in c.verbatim_leaks
    assert c.must_not_redact_violations == ["500 mg"]


def _bundle(text, gold_ents, mnr=None):
    cfg = HarnessConfig.load(env="test")
    eng = PHIEngine(cfg, registry_store=store(), adapters={m: FakeAdapter(m, SPANS) for m in cfg.raw["models"]})
    req = DetectionRequest(request_id="req_eval_1", text=text, registry_context={"subject_context_id": "ctx_test"})
    gold = GoldDocument(request_id=req.request_id, dataset="unit", entities=gold_ents,
                        metadata={"doc_id": "d1", "language": "en", "doc_type": "note", "must_not_redact": mnr or []})
    return cfg, Bundle("unit", "realistic", [req], {req.request_id: gold}, {req.request_id: eng.collect(req, include_disabled=True)}, eng)


def test_scorer_reports_redaction_failure_from_faulty_operator(monkeypatch):
    import harness_eval.scoring as S
    i = TEXT.index("Maria Hernandez")
    cfg, b = _bundle(TEXT, [GoldEntity(annotation_id="g1", entity_type="NAME", start=i, end=i + 15)])
    good = score(b, cfg)
    assert good["outcomes"][0].outcome.startswith("detected") and good["outcomes"][0].redaction_status == "redacted_ok"

    def faulty(req):   # an operator bug: the replacement for the first detection hides only half the span
        rr, vault = redact(req)
        r0 = rr.replacements[0]
        bad = r0.model_copy(update={"original_end": r0.original_start + (r0.original_end - r0.original_start) // 2})
        return rr.model_copy(update={"replacements": [bad] + rr.replacements[1:]}), vault
    monkeypatch.setattr(S, "do_redact", faulty)
    res = score(b, cfg)
    o = res["outcomes"][0]
    assert o.outcome == "redaction_failure" and o.failed_stage == "redaction"
    assert o.detection_status == "detected_full" and o.redaction_status == "redaction_failure"


def test_scorer_views_ci_and_mnr():
    i = TEXT.index("Maria Hernandez")
    j = TEXT.index("Lucia")
    cfg, b = _bundle(TEXT, [GoldEntity(annotation_id="g1", entity_type="NAME", start=i, end=i + 15),
                            GoldEntity(annotation_id="g2", entity_type="RELATIVE_NAME", start=j, end=j + 5)],
                     mnr=[[TEXT.index("daughter"), TEXT.index("daughter") + 8, "daughter"]])
    r = score(b, cfg)
    s = r["summary"]
    assert s["token_leak_rate"] > 0 and s["entity_strict_recall"] is not None and "ci95" in s
    assert s["redaction_checks"]["must_not_redact_violations"] == 0
    rows = {x["entity"]: x for x in r["entity_rows"]}
    assert rows["NAME"]["full_span_recall"] == 1.0 and rows["NAME"]["insufficient_evidence"] is True
    assert rows["NAME"]["miss_rate_upper_95_rule_of_three"] == pytest.approx(1.0)
    lucia = next(o for o in r["outcomes"] if o.annotation_id == "g2")
    assert lucia.outcome == "below_threshold" and lucia.best_rejected["threshold_source"] == "thresholds.model.obi_roberta"


def test_core_chars_match_legacy_definition():
    from harness.evaluation.matching import core_chars as legacy
    from harness.schema import GoldEntity as SG
    cat = Catalog(HarnessConfig.load(env="test"))
    for t, lab in [("Dr. Alan Pierce", "PROVIDER_NAME"), ("MRN 4471-2290", "MRN"), ("member ID CAD123", "MEMBER_ID"),
                   ("Mrs. Lucia Ortiz", "RELATIVE_NAME")]:
        assert core_chars(t, 0, len(t), cat, lab) == legacy(t, SG(start=0, end=len(t), text=t, label=lab))


# ---------------------------------------------------------------------------- manifests / contamination / ledger
def test_manifest_hash_and_role_guards(tmp_path, monkeypatch):
    f = tmp_path / "d.jsonl"
    f.write_text('{"id": "x", "text": "hello"}\n')
    man = tmp_path / "datasets.yaml"
    man.write_text(json.dumps({"manifest_version": 1, "datasets": {"dset": {"path": str(f), "role": "test", "sha256": "0" * 64}}}))
    monkeypatch.setattr(manifests, "MANIFEST", man)
    with pytest.raises(manifests.ManifestError, match="hash"):
        manifests.verify("dset")
    with pytest.raises(manifests.ManifestError, match="refused"):
        manifests.require_role(["dset"], {"dev"}, "calibration")


def test_contamination_detects_near_duplicates(monkeypatch, tmp_path):
    a, b = tmp_path / "dev.jsonl", tmp_path / "test.jsonl"
    doc = "the member called about the refill and asked the care manager to call her daughter back tomorrow morning"
    a.write_text(json.dumps({"id": "a1", "text": doc}) + "\n")
    b.write_text(json.dumps({"id": "b1", "text": doc + " please"}) + "\n" + json.dumps({"id": "b2", "text": "unrelated text " * 8}) + "\n")
    monkeypatch.setattr(manifests, "load", lambda: {"datasets": {"d": {"path": str(a), "role": "dev"},
                                                                 "t": {"path": str(b), "role": "test"}}})
    monkeypatch.setattr(manifests, "entry", lambda i: manifests.load()["datasets"][i])
    hits = contamination.check(0.5)
    assert len(hits) == 1 and hits[0]["a"] == "d:a1" and hits[0]["b"] == "t:b1"


def test_ledger_records_provenance(monkeypatch, tmp_path):
    monkeypatch.setattr(ledger, "LEDGER", tmp_path / "ledger.jsonl")
    cfg = HarnessConfig.load(env="test")
    rid = ledger.record("evaluate", cfg, [{"id": "cadence_text", "role": "test"}], {"x": 1})
    row = json.loads((tmp_path / "ledger.jsonl").read_text())
    assert row["run_id"] == rid and row["test_access"] is True and row["config_sha256"] == cfg.sha256()
    assert row["code_fingerprint"] and row["catalog_version"] == cfg.raw["catalog_version"]


def test_independent_token_crosscheck_agrees_with_scorer(monkeypatch):
    """Two separately written implementations of token-level leak recall must agree."""
    import harness_eval.scoring as S
    from harness_eval import xcheck
    i, j = TEXT.index("Maria Hernandez"), TEXT.index("Lucia")
    gold = [GoldEntity(annotation_id="g1", entity_type="NAME", start=i, end=i + 15),
            GoldEntity(annotation_id="g2", entity_type="RELATIVE_NAME", start=j, end=j + 5)]
    cfg, b = _bundle(TEXT, gold)
    captured = {}
    real = S.redaction_check

    def spy(text, rr, g, mnr, vault=None):
        out = real(text, rr, g, mnr, vault)
        captured["hidden"] = set(out.covered)
        return out
    monkeypatch.setattr(S, "redaction_check", spy)
    r = score(b, cfg)
    prot, tot = xcheck.token_recall(TEXT, [(g.start, g.end) for g in gold], captured["hidden"])
    assert tot == r["document_rows"][0]["tokens"]
    assert 1 - prot / tot == pytest.approx(r["summary"]["token_leak_rate"])
