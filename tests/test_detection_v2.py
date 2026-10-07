"""Detection v2 (2026-10-07): general fixes for span shaping, typing and missing formats.
Each test pins one GENERAL behaviour with fresh fabricated values (never the diagnostic cases)."""
from __future__ import annotations

import pytest

from harness.phi.config import HarnessConfig
from harness.phi.contracts import DetectionEvidence, Operator, RedactionRequest
from harness.phi.engine import PHIEngine
from harness.phi.policy import _context_type, _context_rules, decide
from harness.phi.reconciliation import ReconParams, reconcile
from harness.phi.recognizers.labelled import LabelledValueRecognizer
from harness.phi.recognizers.patterns import VALIDATORS
from harness.phi.redaction import redact
from harness.shield import rules
from test_phi_harness import FakeAdapter, store


def _types(text: str) -> set[tuple[str, str]]:
    return {(p.canonical_label, p.text) for p in rules.detect(text)}


def _ev(eid, ent, s, e, score_type="regex_match", rid="rule_x", score=1.0):
    return DetectionEvidence(evidence_id=eid, entity_type=ent, original_start=s, original_end=e, raw_score=score,
                             score_type=score_type, recognizer_id=rid, recognizer_version="1",
                             source="model" if score_type == "model_confidence" else "rule", threshold_used=0.0,
                             accepted_as_candidate=True)


@pytest.fixture(scope="module")
def eng():
    cfg = HarnessConfig.load(env="test")
    return PHIEngine(cfg, registry_store=store(), adapters={m: FakeAdapter(m, []) for m in cfg.raw["models"]}, self_test=False)


def _detect(eng, text):
    from test_phi_harness import request
    r = eng.analyze(request(text, options={"mode": "detect"}, registry_context=None))
    return [(d.entity_type, text[d.original_start:d.original_end]) for d in r.detections if d.decision != "KEEP"]


# ---------------------------------------------------------------------------- deterministic rules
def test_dotted_dates_never_inside_ip_or_version_numbers():
    assert not any(t == "DATE" for t, _ in _types("login from 10.20.30.40 and 172.16.9.200; v 3.1.4.1"))
    assert ("DOB", "07.04.1990") in _types("DOB 07.04.1990")
    assert ("DATE", "5-6-24") in _types("seen 5-6-24") and not any(t == "DATE" for t, _ in _types("ratio 5-6.24"))


def test_city_state_zip_structure_and_dc():
    got = _types("Office at 12 Birch Street SE, Washington, DC 20004, near Salem, OR 97301.")
    assert {("ADDRESS", "12 Birch Street SE"), ("CITY", "Washington"), ("STATE", "DC"), ("ZIP", "20004"),
            ("CITY", "Salem"), ("STATE", "OR"), ("ZIP", "97301")} <= got
    assert ("STATE", "Washington") not in got


def test_street_names_exclude_function_words():
    got = _types("Moved to Reno, Nevada 89501 by Dr. Hana Ito.")
    assert not any(t == "ADDRESS" for t, _ in got) and ("PROVIDER_NAME", "Dr. Hana Ito") in got


def test_ambiguous_state_codes_only_in_address_position():
    assert ("STATE", "ID") in _types("Clinic in Nampa, ID 83651.")
    assert not any(t == "STATE" for t, _ in _types("Thanks, ID is RX 5510-28 for the claim."))
    assert ("STATE", "DC") in _types("His DC plate KT 2290 expired.")


def test_state_names_inside_person_names():
    assert not any(t == "STATE" for t, _ in _types("Spoke with Darnell Washington and Georgia Pruitt today."))
    assert ("STATE", "Georgia") in _types("She moved to Georgia last year.")


# ---------------------------------------------------------------------------- reconciliation v2
P = ReconParams(True, frozenset({"by", "at", "and", "in"}), frozenset({"MAC", "IP", "ID"}))


def test_bridged_groups_are_split_and_connectors_released():
    text = "Lives in Mesa, AZ 85201 now"
    s = text.index("AZ")
    evs = [_ev("st", "STATE", s, s + 2), _ev("zip", "ZIP", s + 3, s + 8),
           _ev("m", "ADDRESS", s, s + 8, "model_confidence", "model_obi_roberta", 0.9)]
    g = reconcile(text, evs, P)
    assert [(x.start, x.end) for x in g] == [(s, s + 2), (s + 3, s + 8)]
    assert len(reconcile(text, evs, ReconParams())) == 1           # disabled: union as before


def test_same_type_clusters_keep_their_union():
    text = "Patient Lena Ross Ortiz"
    a, b = text.index("Lena"), text.index("Ortiz")
    evs = [_ev("a", "NAME", a, a + 4), _ev("b", "NAME", b, b + 5), _ev("m", "NAME", a, b + 5, "model_confidence", "model_x")]
    assert [(x.start, x.end) for x in reconcile(text, evs, P)] == [(a, b + 5)]


def test_field_labels_trimmed_and_label_only_dropped_unless_typed_by_a_rule():
    text = "MAC 0a:1b:2c:3d:4e:5f and MAC alone"
    s = text.index("0a")
    evs = [_ev("m1", "OTHER_UNIQUE_ID", 0, s + 17, "model_confidence", "model_x"),
           _ev("m2", "FACILITY_NAME", text.rindex("MAC"), text.rindex("MAC") + 3, "model_confidence", "model_x")]
    g = reconcile(text, evs, P)
    assert [(x.start, x.end) for x in g] == [(s, s + 17)]
    t2 = "Nampa, ID 83651"
    assert [(x.start, x.end) for x in reconcile(t2, [_ev("st", "STATE", 7, 9)], P)] == [(7, 9)]


def test_covering_evidence_wins_type_within_same_precedence(eng):
    assert ("IP_ADDRESS", "10.20.30.40") in _detect(eng, "Remote login from IP 10.20.30.40 failed.")


def test_fragments_of_one_identifier_merge(eng):
    cfg = eng.cfg
    text = "Policy KQ/22918-B on file"
    s = text.index("KQ")
    evs = [_ev("a", "OTHER_UNIQUE_ID", s, s + 2), _ev("b", "OTHER_UNIQUE_ID", s + 3, s + 8), _ev("c", "OTHER_UNIQUE_ID", s + 9, s + 10)]
    dets, _ = decide("req_frag", text, evs, cfg, "cadence_default")
    assert [(d.original_start, d.original_end) for d in dets] == [(s, s + 10)]


# ---------------------------------------------------------------------------- contextual typing
def test_relation_and_employer_cues(eng):
    rules_ = _context_rules(eng.cfg)
    t = "Her emergency contact was Noor Haddad; his employer, Bluewater Freight, called."
    assert _context_type(t, t.index("Noor"), t.index("Noor") + 11, "NAME", rules_, True)[0] == "RELATIVE_NAME"
    assert _context_type(t, t.index("Bluewater"), t.index("Bluewater") + 17, "ORGANIZATION", rules_, True)[0] == "EMPLOYER"
    t2 = "Her daughter saw Dr. Noor Haddad."
    assert _context_type(t2, t2.index("Noor"), t2.index("Noor") + 11, "PROVIDER_NAME", rules_, True) is None


def test_model_only_provider_without_role_cue_is_treated_as_patient(eng):
    rules_ = _context_rules(eng.cfg)
    t = "Patient Ana Liu reports dizziness."
    s, e = t.index("Ana"), t.index("Ana") + 7
    assert _context_type(t, s, e, "PROVIDER_NAME", rules_, model_only=True)[0] == "NAME"
    assert _context_type(t, s, e, "PROVIDER_NAME", rules_, model_only=False) is None          # registry / rule support
    t2 = "Seen by cardiologist Ana Liu today."
    assert _context_type(t2, t2.index("Ana"), t2.index("Ana") + 7, "PROVIDER_NAME", rules_, model_only=True) is None


# ---------------------------------------------------------------------------- new formats
@pytest.mark.parametrize("text,expected", [
    ("Adapter MAC: 1c-2d-3e-4f-5a-6b today.", ("DEVICE_ID", "1c-2d-3e-4f-5a-6b")),
    ("Wi-Fi chip reports 1c2d.3e4f.5a6b.", ("DEVICE_ID", "1c2d.3e4f.5a6b")),
    ("Handset IMEI 913450844021025 flagged.", ("DEVICE_ID", "913450844021025")),
    ("Refund to BE80 6186 0025 8170 please.", ("ACCOUNT_ID", "BE80 6186 0025 8170")),
    ("GPS location at the time of transport was 48.85661, 2.35222 per the crew.", ("GEO_COORDINATES", "48.85661, 2.35222")),
    ("Found at 12.9716 N, 77.5946 E.", ("GEO_COORDINATES", "12.9716 N, 77.5946 E")),
    ("Medical license number: OH-ML-30418.", ("LICENSE_CERT_ID", "OH-ML-30418")),
    ("DEA registration TR5521408 verified.", ("LICENSE_CERT_ID", "TR5521408")),
    ("Bank account #00412233 closed.", ("ACCOUNT_ID", "00412233")),
    ("Beneficiary number QZ-88120-L active.", ("MEMBER_ID", "QZ-88120-L")),
    ("VIN reads YV1RS61R512345678 per title.", ("VEHICLE_ID", "YV1RS61R512345678")),
])
def test_new_formats_detected_with_specific_type(eng, text, expected):
    got = _detect(eng, text)       # the span may carry harmless edge punctuation ("#00412233")
    assert any(t == expected[0] and expected[1] in v and len(v) - len(expected[1]) <= 1 for t, v in got), got


@pytest.mark.parametrize("text", [
    "The ratio was 0.5512, 0.4120 across arms.", "Session ran 09:15:30 to 09:45:00.", "Plate count was 310000 today.",
    "Taking into account 120 responses overall.", "Serial readings of 118 and 122.", "The policy changed in 2025.",
])
def test_new_detectors_stay_quiet_on_clinical_text(eng, text):
    assert _detect(eng, text) == []


def test_context_is_left_side_and_bound_to_the_nearest_label(eng):
    got = _detect(eng, "Passport 512908447, SSN 219-09-9999 on file.")
    assert ("SSN", "219-09-9999") in got and not any(t == "SSN" and v == "512908447" for t, v in got)


def test_validators():
    assert VALIDATORS["iban_mod97"]("DE84351499914288117031") and not VALIDATORS["iban_mod97"]("DE84351499914288117032")
    assert VALIDATORS["latlon_valid"]("-33.86785,151.20732") and not VALIDATORS["latlon_valid"]("95.1234, 10.1234")


def test_labelled_recognizer_guards():
    spec = HarnessConfig.load(env="test").raw["recognizers"]["labelled_identifiers"]
    r = LabelledValueRecognizer("labelled_identifiers", spec)
    hit = lambda t: [(h.entity_type, t[h.start:h.end]) for h in r.hits(t)]
    assert hit("License plate TX 4KL-221 seen.") == [("VEHICLE_ID", "TX 4KL-221")]
    assert hit("Updated policy 2026 for all.") == []                   # a year is not an identifier
    assert hit("Account for 12 patients.") == []                       # bridge does not cross 'for'
    assert hit("Taking into account, 250 patients were seen.") == []   # a bare comma does not bind


# ---------------------------------------------------------------------------- token coreference
def _det(eid, ent, s, e):
    from harness.phi.contracts import Detection
    return Detection(detection_id=eid, entity_type=ent, original_start=s, original_end=e, raw_text_available=True,
                     decision_score=1.0, threshold=None, decision="REDACT", confidence_class="high", decision_reason="t",
                     contributing_evidence_ids=[], contributing_recognizers=[], policy_version="t", reconciliation_version="t")


def test_partial_name_mentions_reuse_the_full_names_token():
    text = "Rosa Delgado called. Later Rosa's son and Dr. Ken Ito and Dr. Ito's nurse met Ken Delgado. Delgado left."
    spans = [("NAME", "Rosa Delgado"), ("NAME", "Rosa's"), ("PROVIDER_NAME", "Ken Ito"), ("PROVIDER_NAME", "Ito's"),
             ("NAME", "Ken Delgado"), ("NAME", "Delgado")]
    dets, pos = [], 0
    for i, (ent, v) in enumerate(spans):
        s = text.index(v, pos)
        pos = s + len(v)
        dets.append(_det(f"d{i}", ent, s, s + len(v)))
    rr, _ = redact(RedactionRequest(request_id="r", original_text=text, detections=dets, operator=Operator.TOKENIZE,
                                    coreference_entities=["NAME", "PROVIDER_NAME"]))
    toks = [x.replacement for x in rr.replacements]
    assert toks[0] == toks[1]                     # "Rosa's" -> Rosa Delgado's token
    assert toks[2] == toks[3]                     # "Ito's"  -> Ken Ito's token
    assert toks[5] not in (toks[0], toks[4])      # "Delgado" matches two earlier NAMEs: ambiguous -> new token
    rr2, _ = redact(RedactionRequest(request_id="r", original_text=text, detections=dets, operator=Operator.TOKENIZE))
    assert rr2.replacements[0].replacement != rr2.replacements[1].replacement   # off unless configured


# ---------------------------------------------------------------------------- strict evaluator
def test_strict_scorer_outcomes():
    from types import SimpleNamespace as NS
    from harness_eval.strict_eval import metrics, score_document
    text = "Dr. Ana Liu at MAC 0a:1b:2c:3d:4e:5f"
    s = text.index("0a")
    gold = [{"start": 4, "end": 11, "label": "PROVIDER_NAME", "accept": ["PROVIDER_NAME"]},
            {"start": s, "end": s + 17, "label": "DEVICE_ID", "accept": ["DEVICE_ID"]}]
    d = lambda a, b, t: NS(original_start=a, original_end=b, entity_type=t, decision="REDACT")
    res = score_document(text, gold, [], [d(0, 11, "PROVIDER_NAME"), d(text.index("MAC"), s + 8, "OTHER_UNIQUE_ID")])
    out = {e["label"]: e["outcome"] for e in res["entities"]}
    assert out == {"PROVIDER_NAME": "C", "DEVICE_ID": "P"}          # title is neutral; MAC partially exposed
    assert len(res["false_positives"]) == 1                          # the absorbed "MAC" label
    m = metrics(res["entities"], 1)
    assert m["correct"] == 1 and m["partial"] == 1 and round(m["precision"], 3) == round(1 / 3, 3)


# ---------------------------------------------------------------------------- detection v3 (2026-10-07)
@pytest.mark.parametrize("text,expected", [
    ("Her dental-hygienist license DH-55120 is current.", ("LICENSE_CERT_ID", "DH-55120")),
    ("Paramedic credential reference: EMT-P-30417", ("LICENSE_CERT_ID", "EMT-P-30417")),
    ("Insurance identifier QX-OR-55821904 verified.", ("MEMBER_ID", "QX-OR-55821904")),
    ("Workers'-comp/health-plan identifier WC-77310452", ("MEMBER_ID", "WC-77310452")),
    ("Utah registration TKR-482 was renewed.", ("VEHICLE_ID", "TKR-482")),
    ("Clinic at 910 Elm Crossing today.", ("ADDRESS", "910 Elm Crossing")),
    ("Insurer Northwind Mutual approved it.", ("ORGANIZATION", "Northwind Mutual")),
    ("Seen by Ana P. Ortiz, NP today.", ("PROVIDER_NAME", "Ana P. Ortiz")),
])
def test_v3_formats_and_labels(eng, text, expected):
    assert expected in _detect(eng, text), _detect(eng, text)


def test_credentials_are_not_states_and_places_are_not_providers():
    assert not any(t in ("STATE", "CITY") for t, _ in _types("Signed by Jordan B. Kessler, MD today."))
    assert {("CITY", "Silver Spring"), ("STATE", "MD")} <= _types("She moved to Silver Spring, MD.")
    assert {("CITY", "Baltimore"), ("STATE", "MD"), ("ZIP", "21201")} <= _types("Clinic in Baltimore, MD 21201.")


def test_vehicle_make_model_and_bare_year_are_not_phi(eng):
    got = _detect(eng, "He drives a 2015 Mazda CX-5, Nevada plate 4KD-219.")
    assert ("VEHICLE_ID", "4KD-219") in got and not any("Mazda" in v or v == "2015" for _, v in got)
    assert ("PROVIDER_NAME", "Dr. Henry Ford") in _detect(eng, "Dr. Henry Ford reviewed the car insurance claim.")
    assert _detect(eng, "The 2019 guidelines were updated.") == []


def test_bare_year_rule_is_scoped_to_typed_text():
    from harness.phi.policy import annotate
    cfg = HarnessConfig.load(env="test")
    from test_phi_harness import request
    for modality, rejected in (("typed", True), ("asr", False)):
        req = request("He has been out since 2018.", modality=modality)
        s = req.text.index("2018")
        ev = _ev("y", "DATE", s, s + 4, "model_confidence", "model_obi_roberta", 0.99)
        out = annotate([ev], cfg, req)[0]
        assert (out.rejection_reason == "non_phi") is rejected


def test_initial_and_abbreviation_fragments_merge(eng):
    text = "Seen by Kevin L. Harper at St. Agnes Hospital"
    a, b = text.index("Kevin"), text.index("Harper")
    c, d = text.index("St."), text.index("Agnes")
    evs = [_ev("a", "PROVIDER_NAME", a, a + 7, "model_confidence", "model_x", 0.99),
           _ev("b", "PROVIDER_NAME", b, b + 6, "model_confidence", "model_x", 0.99),
           _ev("c", "FACILITY_NAME", c, c + 2, "model_confidence", "model_x", 0.99),
           _ev("d", "FACILITY_NAME", d, len(text), "model_confidence", "model_x", 0.99)]
    for e in evs:
        e.threshold_used = 0.0
    dets, _ = decide("req_init", text, evs, eng.cfg, "cadence_default")
    assert [(text[x.original_start:x.original_end], x.entity_type) for x in dets] == \
        [("Kevin L. Harper", "PROVIDER_NAME"), ("St. Agnes Hospital", "FACILITY_NAME")]


def test_provider_cue_does_not_leak_across_another_person(eng):
    rules_ = _context_rules(eng.cfg)
    t = "Signed by Dr. Ines Ortega, claims examiner Corey Lamb."
    s = t.index("Corey")
    prev_end = t.index("Ortega") + 6
    assert _context_type(t, s, s + 10, "PROVIDER_NAME", rules_, True, bounds=(prev_end, len(t)))[0] == "NAME"
