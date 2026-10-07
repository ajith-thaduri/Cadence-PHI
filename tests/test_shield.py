from conftest import ent

from harness.evaluation.matching import match_sample
from harness.metrics.safety import doc_safety, safety_summary
from harness.schema import Prediction, Sample
from harness.shield import registry, rules
from harness.shield.reconcile import expand_to_words, reconcile
from harness.shield.redact import redact, restore
from harness.shield.spoken import digit_runs


def labels(text):
    return {(p.text, p.canonical_label) for p in rules.detect(text)}


def test_spoken_digits():
    t = "her phone is five five five, four one zero, two two nine eight ok"
    (s, e, d), = digit_runs(t)
    assert d == "5554102298" and t[s:e].startswith("five") and t[s:e].endswith("eight")
    assert digit_runs("cinco cinco cinco uno dos")[0][2] == "55512"
    assert digit_runs("take one or two tablets") == []


def test_identifier_rules_natural_formats():
    assert ("4829103", "MRN") in labels("MRN 4829103 on file")
    assert ("4829103", "MRN") in labels("medical record number: 4829103.")
    assert ("83749201", "MEMBER_ID") in labels("insurance member number 83749201")
    assert ("1234567893", "NPI") in labels("provider NPI 1234567893")
    assert ("55120934", "ACCOUNT_ID") in labels("account number 55120934")
    assert any(l == "MRN" for _, l in labels("MRN one two three four five six"))
    assert ("257302207", "MEMBER_ID") in labels("Agent: member ID?\nCaller: Sure, it's 257302207.")


def test_contact_and_date_rules():
    L = labels("Call (312) 555-0147 or maria.lopez@example.com, born January 2, 1988; seen 01/02/1988, next Tuesday.")
    assert ("(312) 555-0147", "PHONE") in L and ("maria.lopez@example.com", "EMAIL") in L
    assert ("January 2, 1988", "DOB") in L and ("next Tuesday", "DATE") in L
    assert ("maria dot lopez at example dot com", "EMAIL") in labels("email is maria dot lopez at example dot com")
    assert ("January second nineteen eighty eight", "DATE") in labels("on January second nineteen eighty eight")


def test_over_redaction_negatives():
    for t in ["BP 120/80, take 1/2 tablet", "Clinic open Monday through Friday", "History of Parkinson disease",
              "born in the United States", "Follow up in 3 months", "diagnosed 12 years ago", "pain 6/10"]:
        assert not rules.detect(t), (t, rules.detect(t))


def test_spanish_rules():
    L = labels("NASS: 47 39047534 02. NHC: 2594890. Domicilio: Calle Mayor 15, 2º B. CP: 28013. Fecha: 27-octubre-2016.")
    assert ("47 39047534 02", "MEMBER_ID") in L and ("2594890", "MRN") in L and ("28013", "ZIP") in L
    assert any(l == "ADDRESS" for _, l in L) and any(l == "DATE" for _, l in L)


def test_registry_fuzzy_possessive_and_ids():
    reg = {"member": {"name": "Maria Thompson", "member_ids": ["83749201"], "phone": "3125550147"},
           "providers": [{"name": "Raj Patel"}], "staff": [], "facilities": [{"name": "Valley Memorial", "aliases": []}],
           "employers": [], "contacts": []}
    t = "Tompsson's daughter called. Maria said Patel at Valley Memorial has member number eight three seven four nine two zero one."
    got = {(p.text, p.canonical_label) for p in registry.match(t, reg)}
    assert ("Tompsson", "NAME") in got and ("Maria", "NAME") in got and ("Patel", "PROVIDER_NAME") in got
    assert ("Valley Memorial", "FACILITY_NAME") in got
    assert any(l == "MEMBER_ID" for _, l in got)
    assert not registry.match("the del caso", {"member": {"name": "Ana del Río"}})


def test_reconcile_and_word_expansion():
    t = "Maria Thompson called"
    assert expand_to_words(t, 6, 10) == (6, 14)
    ps = [Prediction(start=0, end=5, text="Maria", native_label="x", canonical_label="NAME"),
          Prediction(start=6, end=11, text="Thomp", native_label="rule:a", canonical_label="NAME")]
    out = reconcile(t, ps)
    assert [(p.text, p.canonical_label) for p in out] == [("Maria", "NAME"), ("Thompson", "NAME")]


def test_redaction_consistent_tokens_and_restore():
    t = "Maria called. MARIA said Carlos will drive."
    spans = [Prediction(start=0, end=5, text="Maria", native_label="m", canonical_label="NAME"),
             Prediction(start=14, end=19, text="MARIA", native_label="m", canonical_label="NAME"),
             Prediction(start=25, end=31, text="Carlos", native_label="m", canonical_label="RELATIVE_NAME")]
    red = redact(t, spans)
    assert red.text == "[NAME_1] called. [NAME_1] said [RELATIVE_NAME_1] will drive."
    assert restore(red) == t


def test_residual_after_shield_and_false_all_clear():
    t = "Maria Thompson phone (312) 555-0147."
    s = Sample(id="d", text=t, entities=[ent(t, "Maria Thompson", "NAME"), ent(t, "(312) 555-0147", "PHONE")])
    d = doc_safety(s, [Prediction(start=0, end=5, text="Maria", native_label="m", canonical_label="NAME")])
    assert d["residual_chars"] == len("Thompson") + 10 and not d["sanitized"]
    d0 = doc_safety(s, [])
    assert d0["false_all_clear"] and d0["residual_fraction"] == 1.0
    summ = safety_summary([d, d0])
    assert summ["complete_document_sanitization_rate"] == 0.0 and summ["false_all_clear_rate"] == 0.5
    assert summ["RESIDUAL_PHI_AFTER_SHIELD"]["worst_document"] == "d"


def test_failure_categories():
    from harness.evaluation.failures import categorise
    t = "uh her daughter carmen said"
    s = Sample(id="x", text=t, entities=[ent(t, "carmen", "RELATIVE_NAME").model_copy(update={"tags": ["contextual", "asr_noise"]})],
               metadata={"subset": "NOISY_ASR", "language": "en", "noise_level": "high"})
    g = match_sample(s, [], lambda a, b: True).gold[0]
    cats, primary = categorise(g, s, chunk_bounds=[20])
    assert primary == "chunk_boundary_failure" and "asr_failure" in cats and "model_missed_entity" in cats
