from conftest import always, pred

from harness.evaluation.matching import core_chars, match_sample
from harness.schema import GoldEntity, IgnoreSpan, Sample


def test_exact_match(sample):
    sm = match_sample(sample, [pred(sample.text, "Maria Thompson")], always)
    g = sm.gold[0]
    assert g.exact and g.lenient and g.overlap and g.full
    assert g.outcome == "exact_match"
    assert sm.preds[0].outcome == "exact_match"


def test_partial_title_excluded_is_full_coverage(sample):
    # "Michael Carter" vs gold "Dr. Michael Carter": partial match, but all core chars covered
    sm = match_sample(sample, [pred(sample.text, "Michael Carter")], always)
    g = sm.gold[1]
    assert not g.exact and g.outcome == "partial_match"
    assert g.lenient          # equal after dropping the title
    assert g.full and g.coverage == 1.0


def test_partial_facility(sample):
    sm = match_sample(sample, [pred(sample.text, "Sunrise Valley")], always)
    g = sm.gold[2]
    assert g.outcome == "partial_match" and not g.full
    assert 0.4 < g.coverage < 0.6
    assert g.partial(0.4) and not g.partial(0.6)


def test_partial_name_character_coverage(sample):
    sm = match_sample(sample, [pred(sample.text, "Maria")], always)
    g = sm.gold[0]
    assert g.overlap and not g.full
    assert abs(g.coverage - 5 / 13) < 1e-9     # "MariaThompson" = 13 core chars


def test_id_affix_excluded_from_core(sample):
    sm = match_sample(sample, [pred(sample.text, "-847291")], always)
    assert sm.gold[3].full


def test_miss_and_spurious(sample):
    sm = match_sample(sample, [pred(sample.text, "spoke")], always)
    assert all(g.outcome == "miss" for g in sm.gold)
    assert sm.preds[0].outcome == "spurious"


def test_strict_label_compatibility(sample):
    compat = lambda native, gold: native == "PATIENT" and gold == "NAME"
    p = pred(sample.text, "Dr. Michael Carter", native="PATIENT")
    sm = match_sample(sample, [p], compat)
    g = sm.gold[1]
    assert g.overlap and g.full            # label-agnostic: redacted
    assert not g.strict_overlap            # label-strict: wrong type
    assert not sm.preds[0].strict_correct


def test_ignore_span_not_false_positive():
    text = "She is a 72-year-old member."
    s = Sample(id="x", text=text, entities=[], ignore_spans=[IgnoreSpan(start=9, end=20, reason="age")])
    sm = match_sample(s, [pred(text, "72-year-old")], always)
    assert sm.preds[0].ignored and sm.preds[0].outcome == "ignored"


def test_core_chars_skip_punctuation():
    text = "call (555) 410-2298 now"
    e = GoldEntity(start=5, end=19, text="(555) 410-2298", label="PHONE")
    assert len(core_chars(text, e)) == 10
