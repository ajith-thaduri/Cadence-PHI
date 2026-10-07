from conftest import always, ent, pred

from harness.evaluation.matching import match_sample
from harness.metrics.core import breakdown, compute_metrics, document_block
from harness.schema import Sample


def _two_docs():
    t1 = "Maria Thompson called (555) 410-2298."
    s1 = Sample(id="a", text=t1, entities=[ent(t1, "Maria Thompson", "NAME"), ent(t1, "(555) 410-2298", "PHONE")],
                metadata={"subset": "A", "language": "en", "source": "synthetic"})
    t2 = "Daniel Rodriguez lives in Example City."
    s2 = Sample(id="b", text=t2, entities=[ent(t2, "Daniel Rodriguez", "NAME"), ent(t2, "Example City", "CITY")],
                metadata={"subset": "B", "language": "es", "source": "synthetic"})
    m1 = match_sample(s1, [pred(t1, "Maria Thompson"), pred(t1, "(555) 410-2298", canonical="PHONE")], always)
    m2 = match_sample(s2, [pred(t2, "Daniel"), pred(t2, "lives")], always)
    return [m1, m2]


def test_document_sanitization():
    d = document_block(_two_docs())
    assert d["complete_document_sanitization_rate"] == 0.5
    assert d["one_plus_missed_entity_rate"] == 0.5      # doc b misses Example City entirely
    assert d["critical_miss_rate"] == 0.0               # CITY is not high-risk


def test_residual_phi_rates():
    m = compute_metrics(_two_docs())
    core_total = 13 + 10 + 15 + 11
    residual = 9 + 11                                     # "Rodriguez" + "ExampleCity"
    assert abs(m["residual_phi_character_rate"] - residual / core_total) < 1e-9
    assert m["residual_phi_entity_rate"] == 0.5          # 2 of 4 not fully covered


def test_precision_recall_f1_and_outcomes():
    m = compute_metrics(_two_docs())
    det = m["detection"]["overlap"]
    assert det["precision"] == 3 / 4 and det["recall"] == 3 / 4
    assert abs(det["f1"] - 0.75) < 1e-9
    assert m["entity_recall"] == 0.5
    assert m["outcomes"]["pred_spurious"] == 1 and m["outcomes"]["gold_miss"] == 1


def test_high_risk_and_per_category():
    m = compute_metrics(_two_docs())
    assert m["high_risk_recall"] == 2 / 3                # NAME, PHONE, NAME(partial)
    assert abs(m["high_risk_false_negative_rate"] - 1 / 3) < 1e-9
    assert m["per_category"]["NAME"]["support"] == 2
    assert m["per_category"]["NAME"]["recall_full"] == 0.5
    assert m["per_category"]["CITY"]["recall_full"] == 0.0


def test_macro_averages_over_present_categories():
    m = compute_metrics(_two_docs())
    # typed recall per category (always-compatible): NAME 1.0 (overlap), PHONE 1.0, CITY 0.0
    assert abs(m["typed_macro"]["recall"] - (1 + 1 + 0) / 3) < 1e-9


def test_breakdown_by_language():
    b = breakdown(_two_docs(), lambda s: s.sample.language)
    assert b["en"]["recall_full"] == 1.0 and b["es"]["recall_full"] == 0.0
