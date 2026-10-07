"""Contract tests for the PHI engine (harness/phi). Offline: a fake model adapter stands in for
ML models, so no downloads. These tests assert CONTRACT correctness, never benchmark scores."""
from __future__ import annotations

import json
import logging

import pytest
from pydantic import ValidationError

from harness.adapters.base import ModelStatus
from harness.phi.config import ConfigError, HarnessConfig
from harness.phi.contracts import (DecisionValue, DetectionEvidence, DetectionOptions, DetectionRequest, DetectionResponse,
                                   Mode, Operator, RedactionRequest, RegistryContext, ScoreType)
from harness.phi.engine import PHIEngine
from harness.phi.normalizers import NORMALIZERS, build_views, normalize_text
from harness.phi.policy import annotate, decide
from harness.phi.recognizers import RegistryRecognizer, RuleFamilyRecognizer
from harness.phi.recognizers.rules import npi_luhn_valid
from harness.phi.redaction import redact
from harness.phi.registry_store import InMemoryRegistryStore, RegistryAccessError
from harness.schema import Prediction


# ---------------------------------------------------------------------------- fakes
class _Trunc:
    num_chunks = 1


class _Raw:
    truncation = _Trunc()

    def __init__(self, preds):
        self.preds = preds


class _Mapping:
    native_labels = ["NAME", "RELATIVE_NAME", "PROVIDER_NAME", "FACILITY_NAME", "DATE", "DOB", "PHONE", "ADDRESS"]

    def to_canonical(self, n):
        return n


class FakeAdapter:
    """Emits fixed (surface, label, score) spans wherever the surface occurs."""
    filterable = True
    status = ModelStatus.LOADED
    device = "cpu"
    mapping = _Mapping()

    def __init__(self, key, spans):
        self.entry = {"revision": f"fake-{key}-rev", "model_id": f"fake/{key}", "adapter": "fake"}
        self.spans = spans
        self.calls = 0

    def max_input_length(self):
        return 512, "tokens"

    def infer_raw(self, text, thr=None):
        self.calls += 1
        out = []
        for surf, lab, sc in self.spans:
            i = text.find(surf)
            while i >= 0:
                out.append(Prediction(start=i, end=i + len(surf), text=surf, native_label=lab, canonical_label=lab, score=sc))
                i = text.find(surf, i + 1)
        return _Raw(out)

    def decode(self, raw, text, thr):
        return [p for p in raw.preds if thr is None or p.score >= thr]


TEXT = ("Maria Hernandez said her DOB is twenty eighth March nineteen eighty five. "
        "Her phone is nine one seven, five five five, oh one two two. Dr. Alan Pierce saw her. Her daughter Lucia drove her.")
SPANS = [("Maria Hernandez", "NAME", 0.9999), ("Lucia", "RELATIVE_NAME", 0.42), ("Alan Pierce", "PROVIDER_NAME", 0.97),
         ("twenty eighth March nineteen eighty five", "DOB", 0.88)]


def store():
    s = InMemoryRegistryStore()
    s.add_subject("ctx_test", {"member": {"name": "Maria Hernandez", "member_ids": ["CAD12345678"], "dob": "1985-03-28"}})
    s.add_directory("provider_directory", "providers_2026_10", [{"name": "Alan Pierce", "npi": "1234567893"}])
    return s


def engine(cfg=None, spans=SPANS, all_models=False):
    cfg = cfg or HarnessConfig.load()
    fakes = {m: FakeAdapter(m, spans) for m in cfg.raw["models"]}
    return PHIEngine(cfg, registry_store=store(), adapters=fakes, load_all_models=all_models)


def request(text=TEXT, **kw):
    base = dict(request_id="req_test_1", text=text, language="en", modality="telephone", source="cadence_call",
                normalization_profile="asr",
                registry_context={"subject_context_id": "ctx_test", "provider_directory_version": "providers_2026_10"},
                options={"mode": "debug", "include_text": True})
    base.update(kw)
    return DetectionRequest(**base)


def det(resp, surface):
    return next(d for d in resp.detections if resp_text(resp, d) == surface)


def resp_text(resp, d):
    return TEXT[d.original_start:d.original_end]


# ---------------------------------------------------------------------------- request validation
def test_request_validation():
    with pytest.raises(ValidationError):
        DetectionRequest(request_id="12345678", text="x")                 # looks like a member id
    with pytest.raises(ValidationError):
        DetectionRequest(request_id="req_1", text="x", language="klingon")
    with pytest.raises(ValidationError):
        DetectionRequest(request_id="req_1", text="x", modality="carrier_pigeon")
    with pytest.raises(ValidationError):                                  # raw records are not allowed in the request
        RegistryContext(subject_context_id={"member": {"name": "Maria"}})
    with pytest.raises(ValidationError):
        DetectionRequest(request_id="req_1", text="x", unknown_field=1)
    r = DetectionRequest(request_id="req_1", text="x", options={"mode": "debug"})
    assert r.options.include_candidates and r.options.include_trace


def test_jsonl_round_trip():
    req = request()
    assert DetectionRequest.model_validate_json(req.model_dump_json()) == req
    resp = engine().analyze(req)
    back = DetectionResponse.model_validate_json(resp.model_dump_json())
    assert back == resp


# ---------------------------------------------------------------------------- the end-to-end contract example
def test_end_to_end_contract_example():
    eng = engine()
    req = request()
    original = str(req.text)
    resp = eng.analyze(req)
    assert req.text == original                                            # source text never mutated
    # normalizer produced a derived view with an exact mapping
    views = {v["view_id"]: v for v in resp.normalization.text_views}
    assert "normalized" in views and views["normalized"]["mapping_quality"] == "exact_segment"
    assert any(t.normalizer_id == "spoken_digits" and t.transformations for t in resp.normalization.traces)
    # final detections point at ORIGINAL text
    by_text = {TEXT[d.original_start:d.original_end]: d for d in resp.detections if d.decision == DecisionValue.REDACT}
    assert "nine one seven, five five five, oh one two two" in by_text            # phone found via the normalized view
    assert by_text["nine one seven, five five five, oh one two two"].entity_type == "PHONE"
    assert "twenty eighth March nineteen eighty five" in by_text
    assert by_text["twenty eighth March nineteen eighty five"].entity_type == "DOB"
    maria = by_text["Maria Hernandez"]
    ev = {e.evidence_id: e for e in resp.evidence}
    chain = [ev[i] for i in maria.contributing_evidence_ids]
    assert {e.recognizer_id for e in chain} >= {"registry_member", "model_obi_roberta"}
    reg = next(e for e in chain if e.recognizer_id == "registry_member" and e.original_end - e.original_start == 15)
    ml = next(e for e in chain if e.recognizer_id == "model_obi_roberta")
    assert reg.score_type == ScoreType.registry_exact and reg.raw_score == 1.0
    assert ml.score_type == ScoreType.model_confidence and ml.raw_score == pytest.approx(0.9999)
    assert ml.model_id == "fake/obi_roberta" and ml.model_revision == "fake-obi_roberta-rev"
    assert ml.threshold_used == 0.5 and ml.threshold_source == "thresholds.model.obi_roberta"
    assert "registry" in maria.decision_reason and "ML" in maria.decision_reason
    assert maria.decision_score_type == "policy_aggregate" and maria.confidence_class == "high"
    assert "model_stanford_v2" in resp.summary.recognizers_disabled
    # redaction operates on original offsets
    red = eng.redact(req.model_copy(update={"options": DetectionOptions(mode=Mode.REDACT)}))
    assert "Maria" not in red.output.sanitized_text and "[PHONE_1]" in red.output.sanitized_text
    assert eng.restore(red.output.vault_id, red.output.sanitized_text) == TEXT


# ---------------------------------------------------------------------------- configuration
def test_aliases_and_entity_selection():
    c = HarnessConfig.load()
    c.only_entities(["PERSON", "DOB", "PHONE", "MRN"])
    assert set(c.enabled_entities) == {"NAME", "RELATIVE_NAME", "CARE_TEAM_MEMBER", "DOB", "PHONE", "MRN"}
    with pytest.raises(ConfigError):
        c.only_entities(["NOT_AN_ENTITY"])


def test_threshold_precedence():
    c = HarnessConfig.load()
    f = lambda e="NAME", st="model_confidence": c.threshold_for(e, "model_obi_roberta", "obi_roberta", st)
    assert f() == (0.5, "thresholds.model.obi_roberta")
    c.set_value("thresholds.recognizer.model_obi_roberta", "0.9")
    assert f()[1] == "thresholds.model.obi_roberta"                            # model beats recognizer default
    c.set_value("thresholds.entity.NAME", "0.7")
    assert f() == (0.7, "thresholds.entity.NAME")                              # entity beats model
    c.set_value("thresholds.model_entity.obi_roberta.NAME", "0.2")
    assert f() == (0.2, "thresholds.model_entity.obi_roberta.NAME")            # model x entity beats all
    # entity / model thresholds never drop deterministic evidence
    assert c.threshold_for("NAME", "rule_titled_name", None, "regex_match") == (0.0, "thresholds.deterministic_default")
    c.set_value("thresholds.recognizer.rule_titled_name", "0.95")
    assert c.threshold_for("NAME", "rule_titled_name", None, "regex_match") == (0.95, "thresholds.recognizer.rule_titled_name")


def test_invalid_threshold_rejected():
    c = HarnessConfig.load()
    c.set_value("thresholds.model.obi_roberta", "1.5")
    with pytest.raises(ConfigError):
        c.validate()


def test_entity_enable_disable_config_and_request():
    eng = engine()
    c = HarnessConfig.load()
    c.set_entities(["PHONE"], False)
    resp = eng.analyze(request(), cfg=c)
    assert not any(d.entity_type == "PHONE" and d.decision == DecisionValue.REDACT for d in resp.detections)
    assert any(e.rejection_reason == "entity_disabled" for e in resp.evidence)
    resp = eng.analyze(request(enabled_entities=["PERSON"]))
    assert {d.entity_type for d in resp.detections if d.decision == DecisionValue.REDACT} <= {"NAME", "RELATIVE_NAME", "CARE_TEAM_MEMBER"}


def test_recognizer_enable_disable():
    eng = engine()
    c = HarnessConfig.load()
    c.set_recognizers(["rule_phone", "rule_spoken_number", "conv_spoken_id", "conv_partial_phone"], False)
    resp = eng.analyze(request(), cfg=c)
    assert not any(d.entity_type == "PHONE" and d.decision == DecisionValue.REDACT for d in resp.detections)
    resp = eng.analyze(request(enabled_recognizers=["registry_member"], enabled_models=[]))   # models have their own allow-list
    assert all(set(d.contributing_recognizers) <= {"registry_member"} for d in resp.detections if d.decision == DecisionValue.REDACT)


def test_model_enable_disable():
    eng = engine(all_models=True)
    resp = eng.analyze(request(enabled_models=[]))
    assert not any(e.source == "model" for e in resp.evidence)
    resp = eng.analyze(request(enabled_models=["obi_roberta"]))
    assert any(e.recognizer_id == "model_obi_roberta" for e in resp.evidence)
    assert not any(e.recognizer_id == "model_stanford_v2" for e in resp.evidence)   # disabled in config


def test_presets_are_configuration_only():
    c = HarnessConfig.load()
    c.apply_preset("RULES_ONLY")
    assert c.enabled_models == [] and not c.recognizer_enabled("registry_member") and c.recognizer_enabled("rule_phone")
    c = HarnessConfig.load()
    c.apply_preset("OBI_FULL")
    assert c.recognizer_enabled("conv_spoken_id") and c.enabled_models == ["obi_roberta"]


# ---------------------------------------------------------------------------- evidence / policy
def test_raw_score_and_score_type_preserved():
    resp = engine().analyze(request())
    types = {e.score_type for e in resp.evidence}
    assert {"model_confidence", "registry_exact", "regex_match"} <= types
    lucia = next(e for e in resp.evidence if e.recognizer_id == "model_obi_roberta" and TEXT[e.original_start:e.original_end] == "Lucia")
    assert lucia.raw_score == pytest.approx(0.42) and lucia.accepted_as_candidate is False
    assert lucia.rejection_reason == "below_threshold" and lucia.threshold_used == 0.5


def test_below_threshold_keep_then_model_entity_override():
    eng = engine()
    req = request()
    lucia = lambda r: next(d for d in r.detections if TEXT[d.original_start:d.original_end] == "Lucia")
    assert lucia(eng.analyze(req)).decision == DecisionValue.KEEP
    c = HarnessConfig.load()
    c.set_value("thresholds.model_entity.obi_roberta.RELATIVE_NAME", "0.4")
    d = lucia(eng.analyze(req, cfg=c))
    assert d.decision == DecisionValue.REDACT and d.threshold == 0.4


def test_review_band_policy():
    c = HarnessConfig.load()
    resp = engine(c).analyze(request(policy_profile="research"))        # review_band 0.1: 0.42 is within [0.4, 0.5)
    lucia = next(d for d in resp.detections if TEXT[d.original_start:d.original_end] == "Lucia")
    assert lucia.decision == DecisionValue.REVIEW and lucia.redaction_action == "TOKENIZE"


def test_overlap_resolution_union_and_precedence():
    t = "Seen by Dr. Alan Pierce today"
    a = DetectionEvidence(evidence_id="ev_a", entity_type="PROVIDER_NAME", original_start=12, original_end=16, raw_score=0.9,
                          score_type="model_confidence", recognizer_id="model_obi_roberta", recognizer_version="x",
                          model_id="m", source="model")
    b = DetectionEvidence(evidence_id="ev_b", entity_type="NAME", original_start=12, original_end=23, raw_score=1.0,
                          score_type="registry_exact", recognizer_id="registry_member", recognizer_version="1", source="registry")
    c = HarnessConfig.load()
    ann = annotate([a, b], c, None, [])
    dets, _ = decide("req_x", t, ann, c, "cadence_default", include_text=True)
    assert len(dets) == 1 and dets[0].text == "Alan Pierce"
    # precedence picks registry_exact; the model's PROVIDER_NAME covers only "Alan", so it may NOT refine the type
    assert dets[0].entity_type == "NAME" and "type conflict" in dets[0].decision_reason
    assert set(dets[0].contributing_evidence_ids) == {"ev_a", "ev_b"}
    # a subtype covering the whole selected span does refine it (NAME -> PROVIDER_NAME)
    full = a.model_copy(update={"evidence_id": "ev_c", "original_end": 23})
    dets, _ = decide("req_x", t, annotate([full, b], c, None, []), c, "cadence_default", include_text=True)
    assert dets[0].entity_type == "PROVIDER_NAME"


def test_partial_token_expanded_to_word():
    t = "Thompson arrived"
    e = DetectionEvidence(evidence_id="ev_t", entity_type="NAME", original_start=0, original_end=4, raw_score=0.9,
                          score_type="model_confidence", recognizer_id="model_obi_roberta", recognizer_version="x",
                          model_id="m", source="model")
    c = HarnessConfig.load()
    dets, _ = decide("req_x", t, annotate([e], c, None, []), c, "cadence_default", include_text=True)
    assert dets[0].text == "Thompson"


def test_no_recognizer_mutates_text_and_offsets_valid():
    eng = engine()
    req = request()
    col = eng.collect(req, include_disabled=True)
    assert req.text == TEXT
    for e in col.evidence:
        assert 0 <= e.original_start < e.original_end <= len(TEXT)
    for v in col.views:
        if v.view_id == "original":
            assert v.text == TEXT


def test_deterministic_reproducibility():
    eng = engine()
    a, b = eng.analyze(request()), eng.analyze(request())
    strip = lambda r: json.loads(r.model_dump_json(exclude={"timings", "trace"}))
    assert strip(a) == strip(b)
    assert [d.detection_id for d in a.detections] == [d.detection_id for d in b.detections]


def test_long_text_offsets_stay_valid():
    t = ("Filler sentence about care. " * 400) + "Call 555-013-2244. " + ("More filler. " * 200)
    resp = engine().analyze(request(text=t, registry_context=None))
    ph = [d for d in resp.detections if d.entity_type == "PHONE"]
    assert ph and all(t[d.original_start:d.original_end] == "555-013-2244" for d in ph)


def test_english_only_profile():
    from harness.phi.config import UnsupportedLanguageError
    eng = engine()
    assert eng.langs == ["en"]                                            # no Spanish recognizers are even built
    for lang in ("es-US", "mixed"):                                        # refused, fail-closed (never passed through)
        r = eng.analyze(request(text="Paciente con NHC 4455667.", language=lang, registry_context=None))
        assert r.status == "blocked" and r.failure.failure_class == "unsupported_language" and r.detections == []
    with pytest.raises(UnsupportedLanguageError):
        eng.cfg.resolve_language("es")
    assert eng.analyze(request(language="en-US")).input.resolved_language == "en"


# ---------------------------------------------------------------------------- normalization
@pytest.mark.parametrize("src,expect", [
    ("five five five oh one two two", "5550122"),
    ("the twenty eighth", "28th"),
    ("nineteen fifty two", "1952"),
    ("D-E-L-A-C-R-O-I-X", "Delacroix"),
    ("V as in Victor, A, N", "Van"),
    ("j dot rivera at example dot com", "j.rivera@example.com"),
    ("fourteen fifty Maple Street", "1450 Maple Street"),
    ("so, um, the the clinic", "so, the clinic"),
])
def test_normalizers(src, expect):
    assert expect in normalize_text(src, list(NORMALIZERS)).text


def test_normalized_to_original_offset_mapping():
    t = "My cell is five five five, oh one two two four four, thanks"
    v = normalize_text(t, list(NORMALIZERS))
    i = v.text.index("555012244")
    s, e, used = v.to_original(i, i + 9)
    assert t[s:e] == "five five five, oh one two two four four" and used == ["spoken_digits_v1"]
    j = v.text.index("thanks")
    assert t[slice(*v.to_original(j, j + 6)[:2])] == "thanks"                 # identity segments map 1:1


def test_normalization_trace_and_ablation():
    views, traces = build_views(TEXT, ["spoken_digits"])
    assert [t.normalizer_id for t in traces] == ["spoken_digits"] and traces[0].version == "1"
    assert traces[0].input_sha256 != traces[0].output_sha256
    assert views[0].text == TEXT
    # profile 'none': no derived view, so the spoken phone is not found by the digit rules
    resp = engine().analyze(request(normalization_profile="none"))
    assert resp.normalization.normalizers_applied == [] and len(resp.normalization.text_views) == 1


def test_disabled_normalizer_evidence_rejected():
    c = HarnessConfig.load()
    eng = engine(c)
    col = eng.collect(request(), include_disabled=True)
    off = c.copy()
    off.raw["normalizers"]["spoken_digits"]["enabled"] = False
    ann = annotate(col.evidence, off, request(), off.normalizers_for("asr"))
    via = [e for e in ann if any(t.startswith("spoken_digits") for t in e.metadata.get("transformations", []))
           and off.recognizer_enabled(e.recognizer_id)]
    assert via and all(e.rejection_reason == "normalizer_disabled" for e in via)


def test_spelled_letters_ignores_ordinary_words():
    assert "A I" not in normalize_text("I am a person who said a b c to a child", ["spelled_names"]).text


# ---------------------------------------------------------------------------- registry
def test_registry_recognizer_match_types():
    g = RegistryRecognizer("registry_member", ["member"], {"member": {"name": "Maria Hernandez"}}, {"subject": "ctx"})
    hits = {(h.start, h.end): h for h in g.hits("Maria Hernandez and Mario Hernandes")}
    assert hits[(0, 15)].score_type == ScoreType.registry_exact and hits[(0, 15)].metadata["match_type"] == "exact"
    assert hits[(20, 35)].score_type == ScoreType.registry_fuzzy
    assert hits[(0, 15)].metadata["registry_type"] == "member"
    with pytest.raises(ValueError):
        RegistryRecognizer("registry_member", ["member"], {"simulated_from_gold": True})


def test_registry_scoping():
    s = store()
    s.add_subject("ctx_other", {"member": {"name": "Zelda Quintero"}})
    reg, versions = s.resolve(RegistryContext(subject_context_id="ctx_test"))
    assert "Zelda" not in json.dumps(reg) and versions == {"subject": "ctx_test"}
    with pytest.raises(RegistryAccessError):
        s.resolve(RegistryContext(subject_context_id="ctx_missing"))
    resp = engine().analyze(request(text="Zelda Quintero called.", registry_context={"subject_context_id": "ctx_test"}))
    assert not any(e.source == "registry" for e in resp.evidence)              # other subjects' data never matched


def test_rule_recognizer_score_types_and_checksum():
    hits = RuleFamilyRecognizer("rule_phone", "phones").hits("Call 555-013-2244 today")
    assert hits and hits[0].score_type == ScoreType.regex_match
    assert npi_luhn_valid("1234567893") and not npi_luhn_valid("1234567890")


# ---------------------------------------------------------------------------- redaction
def test_redaction_offsets_operators_and_vault():
    eng = engine()
    resp = eng.analyze(request())
    dets = [d for d in resp.detections if d.decision == DecisionValue.REDACT]
    rr, vault = redact(RedactionRequest(request_id="req_test_1", original_text=TEXT, detections=dets, operator=Operator.TOKENIZE))
    for rep in rr.replacements:
        assert rr.sanitized_text[rep.output_start:rep.output_end] == rep.replacement
        assert rep.replacement.startswith("[")
    assert vault.restore(rr.sanitized_text) == TEXT
    assert "Maria" not in rr.model_dump_json()                                # token map is not in the response
    rr2, _ = redact(RedactionRequest(request_id="req_test_1", original_text=TEXT, detections=dets, operator=Operator.REDACT))
    assert "[NAME]" in rr2.sanitized_text
    with pytest.raises(ValueError):
        redact(RedactionRequest(request_id="r", original_text=TEXT, detections=dets + dets[:1]))   # overlapping input refused


def test_consistent_tokenisation():
    t = "Maria called. Later Maria called again from 555-013-2244."
    eng = engine(spans=[("Maria", "NAME", 0.9)])
    r = eng.redact(request(text=t, registry_context=None, options={"mode": "redact"}))
    assert r.output.sanitized_text.count("[NAME_1]") == 2 and "[PHONE_1]" in r.output.sanitized_text
    assert eng.restore(r.output.vault_id, "Call [NAME_1] back") == "Call Maria back"


# ---------------------------------------------------------------------------- privacy
def test_production_response_and_logs_contain_no_phi(caplog):
    eng = engine()
    with caplog.at_level(logging.DEBUG):
        resp = eng.analyze(request(options={"mode": "detect"}))        # production defaults
    blob = resp.model_dump_json()
    for secret in ("Maria", "Hernandez", "Lucia", "Pierce", "nine one seven"):
        assert secret not in blob
        assert secret not in caplog.text
    assert resp.evidence == [] and all(d.text is None for d in resp.detections)


def test_model_cache_reused():
    c = HarnessConfig.load()
    fakes = {m: FakeAdapter(m, SPANS) for m in c.raw["models"]}
    caches: dict = {}
    eng = PHIEngine(c, registry_store=store(), adapters=fakes, model_caches=caches)
    before = fakes["obi_roberta"].calls                                      # startup self-test already ran the model
    eng.analyze(request())
    eng.analyze(request())
    assert fakes["obi_roberta"].calls - before == 1


# ---------------------------------------------------------------------------- conversational rules (dev-derived)
from harness.phi.recognizers import conversational as CV  # noqa: E402


def _hits(fn, t):
    return [(t[s:e], lab) for s, e, lab, _, _ in fn(t)]


@pytest.mark.parametrize("text,surface,label", [
    ("Account is B L, L as in Lima, seven seven zero, four eight three.", "B L, L as in Lima, seven seven zero, four eight three", "ACCOUNT_ID"),
    ("Member ID? It's M C, zero zero four, five five, nine one, two, sorry, scratch that, nine one three.",
     "M C, zero zero four, five five, nine one, two, sorry, scratch that, nine one three", "MEMBER_ID"),
    ("¿Su número de miembro? Es eme de, siete cuatro, no, perdón, siete tres, cero cero, ocho ocho, uno cinco.",
     "eme de, siete cuatro, no, perdón, siete tres, cero cero, ocho ocho, uno cinco", "MEMBER_ID"),
    ("Su teléfono es el dos treinta y nueve, cinco cinco cinco, cero uno, sesenta y tres.",
     "dos treinta y nueve, cinco cinco cinco, cero uno, sesenta y tres", "PHONE"),
])
def test_conv_spoken_id(text, surface, label):
    assert (surface, label) in _hits(CV.conv_spoken_id, text)


@pytest.mark.parametrize("text", [
    "Blood pressure one forty over ninety, A1c 9.4, BP 118/74.",
    "La clínica abre los sábados de ocho a doce.",
    "They have the eighteenth at seven thirty a.m.",
    "Take one unit of insulin.",
])
def test_conv_spoken_id_negatives(text):
    assert _hits(CV.conv_spoken_id, text) == []


def test_conv_partial_phone_and_dates_and_address():
    assert ("cero uno dos ocho", "PHONE") in _hits(CV.conv_partial_phone, "¿Y el teléfono, el que termina en cero uno dos ocho?")
    assert ("four, twelve, ninety-one", "DOB") in _hits(CV.conv_ordinal_date, "Date of birth?\nMEMBER: It's four, twelve, ninety-one.")
    assert any(s == "el sábado diecisiete" for s, _ in _hits(CV.conv_ordinal_date, "¿Le serviría el sábado diecisiete?"))
    assert _hits(CV.conv_ordinal_date, "Most people forget the second.") == []
    assert ("apartment, um, eleven C", "ADDRESS") in _hits(CV.conv_spoken_address, "Yes, apartment, um, eleven C.")
    assert ("oh two seven two four", "ZIP") in _hits(CV.conv_spoken_address, "Fall River, Massachusetts, oh two seven two four")
    assert _hits(CV.conv_spoken_address, "Please don't leave it on the step.") == []


def test_conv_language_and_spelled():
    assert ("Spanish", "LANGUAGE") in _hits(CV.conv_language, "I'll note that she prefers Spanish.")
    assert _hits(CV.conv_language, "A Spanish-speaking nurse came. Necesita intérprete de quechua o español.") == []
    assert ("R A M A N", "NAME") in _hits(CV.conv_spelled_name, "Can you spell it? R A M A N.")
    assert _hits(CV.conv_spelled_name, "Advance directive on file, D N R.") == []


@pytest.mark.parametrize("src,expect", [
    ("Email is easiest, e brandt at example dot org.", "ebrandt@example.org"),
    ("it's Priya dot raman nine one... at example dot net", "priya.raman91@example.net"),
    ("yoleisy punto montes ochenta y ocho arroba example punto com", "yoleisy.montes88@example.com"),
    ("email me, g, h, e, double r, i, n, g, t, o, n at example dot org", "gherrington@example.org"),
])
def test_spoken_email_local_parts(src, expect):
    assert expect in normalize_text(src, ["spoken_email"]).text


# ---------------------------------------------------------------------------- harness-completion tests
from harness.phi.contracts import (MappingQuality, OffsetSegment, RedactionResponse, RejectionReason,  # noqa: E402
                                   TextView)
from harness.phi.recognizers.base import RecognitionContext, evidence_from_result  # noqa: E402


def test_original_text_alias_and_session_metadata():
    r = DetectionRequest(request_id="req_a", original_text="Call 555-013-2244", metadata={"session": "s1", "turn": 3})
    assert r.text == r.original_text == "Call 555-013-2244" and r.metadata["turn"] == 3
    with pytest.raises(ValidationError):
        DetectionRequest(request_id="req_a", original_text="x", metadata={"nested": {"phi": "no"}})


def test_response_validation():
    resp = engine().analyze(request())
    data = json.loads(resp.model_dump_json())
    data["detections"][0]["decision"] = "MAYBE"
    with pytest.raises(ValidationError):
        DetectionResponse.model_validate(data)
    for d in resp.detections:                                               # coordinates are original-text, half-open
        assert 0 <= d.original_start < d.original_end <= len(TEXT)


def test_approximate_mapping_cannot_drive_redaction():
    text = "call me at six one four five five five oh one two two"
    view = TextView(view_id="approx", transformation_ids=["lossy_v1"], mapping_quality=MappingQuality.approximate,
                    text="call me at 614-555-0122", segments=[
                        OffsetSegment(derived_start=0, derived_end=11, original_start=0, original_end=11),
                        OffsetSegment(derived_start=11, derived_end=23, original_start=11, original_end=len(text),
                                      transformation_id="lossy_v1")])
    rec = RuleFamilyRecognizer("rule_phone", "phones")
    ctx = RecognitionContext("req_x", "en")
    evs = [e for e in (evidence_from_result(r, ctx, view) for r in rec.analyze(view.text, ["PHONE"])) if e]
    assert evs and all(e.metadata.get("approximate_mapping") for e in evs)
    c = HarnessConfig.load()
    ann = annotate(evs, c, None, None)
    assert all(e.rejection_reason == RejectionReason.approximate_mapping and not e.accepted_as_candidate for e in ann)
    dets, _ = decide("req_x", text, ann, c, "cadence_default")
    assert not any(d.decision == DecisionValue.REDACT for d in dets)       # supporting evidence only


def test_engine_redact_with_redaction_request_and_vault_isolation(caplog):
    eng = engine()
    resp = eng.analyze(request(options={"mode": "detect"}))
    with caplog.at_level(logging.DEBUG):
        rr = eng.redact(RedactionRequest(request_id="req_test_1", original_text=TEXT, detections=resp.detections))
    assert isinstance(rr, RedactionResponse) and rr.vault_id
    blob = rr.model_dump_json()
    for secret in ("Maria", "Hernandez", "Pierce"):
        assert secret not in rr.sanitized_text and secret not in blob and secret not in caplog.text
    assert "tokens" not in json.loads(blob) and "vault" not in json.loads(blob)   # no token map in the response
    assert eng.restore(rr.vault_id, rr.sanitized_text) == TEXT
    other = engine()                                                         # vaults never leave the engine that made them
    with pytest.raises(KeyError):
        other.restore(rr.vault_id, rr.sanitized_text)


def test_batch_vs_single_document_consistency():
    eng = engine()
    reqs = [request(request_id=f"req_b_{i}", text=t) for i, t in enumerate([TEXT, "Lucia called from 555-013-2244.", TEXT[:80]])]
    single = [eng.analyze(r) for r in reqs]
    batch = [eng.analyze(r, collected=eng.collect(r, include_disabled=True)) for r in reqs]   # what the analyze CLI does
    strip = lambda x: [(d.original_start, d.original_end, d.entity_type, d.decision, d.decision_score) for d in x.detections]
    assert [strip(a) for a in single] == [strip(b) for b in batch]


def test_adapter_weights_shared_across_engines(monkeypatch):
    import harness.phi.engine as E
    built = []

    def fake_build(key, device):
        a = FakeAdapter(key, SPANS)
        built.append(key)
        return a
    monkeypatch.setattr("harness.adapters.factory.build_adapter", fake_build)
    monkeypatch.setattr(E, "_ADAPTER_CACHE", {})
    c = HarnessConfig.load()
    c.raw["models"]["obi_roberta"].pop("weights_sha256", None)     # caching under test, not integrity (test_failsafe)
    PHIEngine(c, registry_store=store())
    PHIEngine(c, registry_store=store())
    assert built == ["obi_roberta"]                                          # loaded once per process


def test_allow_list_rejects_configured_non_phi_terms():
    c = HarnessConfig.load()
    c.raw["allow_list"] = ["Riverside Clinic"]
    t = "Seen at Riverside Clinic by Maria Hernandez."
    resp = engine(c, spans=[("Riverside Clinic", "FACILITY_NAME", 0.99), ("Maria Hernandez", "NAME", 0.99)]).analyze(
        request(text=t, registry_context=None))
    red = {t[d.original_start:d.original_end] for d in resp.detections if d.decision == DecisionValue.REDACT}
    assert "Riverside Clinic" not in red and "Maria Hernandez" in red
    assert any(e.rejection_reason == "allow_listed" for e in resp.evidence)


def test_manual_analyze_tool_uses_engine_and_attributes_misses(tmp_path):
    from argparse import Namespace
    from harness_eval import analyze as A
    (tmp_path / "doc1.txt").write_text(TEXT)
    gold = [{"doc_id": "doc1", "entities": [{"entity_type": "NAME", "start": 0, "end": 15, "text": "Maria Hernandez"},
                                            {"entity_type": "RELATIVE_NAME", "start": TEXT.index("Lucia"),
                                             "end": TEXT.index("Lucia") + 5}]}]
    (tmp_path / "gold.jsonl").write_text(json.dumps(gold[0]) + "\n")
    args = Namespace(input=None, input_dir=str(tmp_path), subject=None, language="en", modality="telephone",
                     normalization_profile="asr", policy_profile="cadence_default")
    docs = A.collect_inputs(args)
    assert [d.doc_id for d in docs] == ["doc1"]
    eng = engine()
    res = A.run(eng, docs, redact=True, operator=None)
    golds = A.read_gold(tmp_path / "gold.jsonl", docs, "exact")
    g = A.score_gold(eng, res, golds, eng.cfg)
    outcomes = {o.entity_type: o.outcome for o in g["per_doc"][docs[0].request.request_id]}
    assert outcomes["NAME"].startswith("detected") and outcomes["RELATIVE_NAME"] == "below_threshold"
    A.write_outputs(tmp_path / "out", res, eng.cfg, True, g)
    rep = (tmp_path / "out" / "report.md").read_text()
    assert "below_threshold" in rep and "Redacted text" in rep
    assert (tmp_path / "out" / "redacted" / "doc1.txt").read_text().count("[") >= 3
    A.write_outputs(tmp_path / "masked", res, eng.cfg, False, g)          # --mask-text: no span text in outputs
    assert "Hernandez" not in (tmp_path / "masked" / "detections.csv").read_text()
