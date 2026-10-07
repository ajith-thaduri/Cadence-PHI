import numpy as np
import pytest

from harness.decision.asr import map_entities, telephone_channel
from harness.decision.experiment import materiality, materially_lower
from harness.decision.markup import parse


def test_markup_parse_offsets_and_unknown_label():
    t, e = parse("Hi [[NAME:Rosa]], call [[PHONE:three one two]].")
    assert t == "Hi Rosa, call three one two." and t[e[1]["start"]:e[1]["end"]] == "three one two"
    with pytest.raises(ValueError):
        parse("[[PERSON:x]]")


def test_asr_alignment_maps_digits_and_substitutions():
    ref = "my number is three one two five five five and doctor Okafor"
    ents = [{"start": 13, "end": 41, "text": ref[13:41], "label": "PHONE"},
            {"start": 53, "end": 59, "text": "Okafor", "label": "PROVIDER_NAME"}]
    hyp = "My number is 312-555 and Dr. Faraco"
    m, d = map_entities(ref, hyp, ents)
    assert not d
    assert m[0]["text"] == "312-555" and m[1]["text"] == "Faraco" and m[1]["asr_altered"]


def test_asr_deleted_entity_is_dropped():
    m, d = map_entities("call Rosa now", "call now", [{"start": 5, "end": 9, "text": "Rosa", "label": "NAME"}])
    assert not m and d[0]["text"] == "Rosa"


def test_telephone_channel_shape_and_band():
    sr = 16000
    x = (np.sin(2 * np.pi * 1000 * np.arange(sr) / sr) * 8000).astype(np.int16)
    y = telephone_channel(x, sr, 1)
    assert len(y) == len(x) and np.isfinite(y).all()


def _r(mean, p95=0.1, cat=None, sp=10, over=0.001):
    return {"residual_mean": mean, "residual_p95": p95, "per_category": cat or {}, "spurious": sp, "over_redaction": over}


def test_materiality_small_gain_not_material():
    m = materiality(_r(0.020), _r(0.018), {})          # 0.2 pp, 10% relative
    assert not m["benefit"]
    assert not materially_lower(0.020, 0.018)


def test_materiality_large_gain_material_with_guards():
    m = materiality(_r(0.030), _r(0.015), {})          # 50% relative, 1.5 pp
    assert m["M1_mean_residual"] and m["passes_before_latency"]
    bad = materiality(_r(0.030), _r(0.015, over=0.02), {})
    assert bad["benefit"] and not bad["passes_before_latency"]    # over-redaction guard


def test_materiality_critical_category():
    a = _r(0.02, cat={"PHONE": {"support": 20, "recall": 0.8, "fn": 4}})
    ab = _r(0.019, cat={"PHONE": {"support": 20, "recall": 1.0, "fn": 0}})
    assert materiality(a, ab, {})["M3_critical_category"] == ["PHONE"]
