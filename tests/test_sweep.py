import numpy as np

from harness.adapters.base import RawOutput
from harness.adapters.gliner_adapter import GLiNERAdapter, _resolve_overlaps
from harness.adapters.hf_token_classifier import HFTokenClassificationAdapter
from harness.label_mapping import LabelMapping
from harness.metrics.sweep import select_threshold
from harness.schema import TruncationInfo

TI = TruncationInfo(strategy="none", max_supported=512, unit="x", input_length=3, truncated=False)


def test_select_threshold_max_f2_ties_prefer_lower():
    rows = [{"threshold": None, "f2": 0.99}, {"threshold": 0.3, "f2": 0.8},
            {"threshold": 0.1, "f2": 0.8}, {"threshold": 0.5, "f2": 0.7}]
    assert select_threshold(rows)["threshold"] == 0.1      # argmax (None) is not selectable


def _gliner_stub():
    a = GLiNERAdapter.__new__(GLiNERAdapter)
    a.mapping = LabelMapping.load("mednotes_gliner")
    return a


def test_gliner_decode_is_monotone_filter():
    a = _gliner_stub()
    raw = RawOutput(payload=[{"start": 0, "end": 5, "label": "person", "score": 0.15},
                             {"start": 6, "end": 9, "label": "dates", "score": 0.55}], truncation=TI)
    text = "Maria 1/2"
    assert len(a.decode(raw, text, 0.1)) == 2
    assert [p.text for p in a.decode(raw, text, 0.5)] == ["1/2"]
    assert a.decode(raw, text, 0.9) == []


def test_chunk_overlap_resolution():
    ents = [{"start": 0, "end": 10, "label": "person", "score": 0.9},
            {"start": 0, "end": 10, "label": "person", "score": 0.8},
            {"start": 5, "end": 15, "label": "address", "score": 0.5},
            {"start": 20, "end": 25, "label": "dates", "score": 0.4}]
    out = _resolve_overlaps(ents)
    assert [(e["start"], e["score"]) for e in out] == [(0, 0.9), (20, 0.4)]


def _hf_stub(model="stanford_base", tags=("O", "PATIENT", "DATE"), scheme="IO"):
    a = HFTokenClassificationAdapter.__new__(HFTokenClassificationAdapter)
    a.mapping = LabelMapping.load(model)
    a.types = sorted({HFTokenClassificationAdapter._split(t, scheme)[1] for t in tags if t != "O"})
    a.tag_type = np.array([0 if t == "O" else 1 + a.types.index(HFTokenClassificationAdapter._split(t, scheme)[1]) for t in tags])
    a.tag_begin = np.array([t != "O" and HFTokenClassificationAdapter._split(t, scheme)[0] in ("B", "U") for t in tags])
    return a


def test_io_decoding_threshold_and_merging():
    """IO-scheme (no BIO) decoding: adjacent same-label wordpieces merge into one span."""
    text = "Maria Thompson on Monday"
    a = _hf_stub()
    offsets = np.array([[0, 5], [6, 10], [10, 14], [15, 17], [18, 24]])
    probs = np.array([[0.1, 0.9, 0.0], [0.2, 0.8, 0.0], [0.6, 0.4, 0.0], [0.99, 0.0, 0.01], [0.3, 0.0, 0.7]])
    raw = RawOutput(payload={"offsets": offsets, "probs": probs}, truncation=TI)
    am = a.decode(raw, text, None)                       # argmax: "Thom|pson" splits at the O token
    assert [(p.text, p.native_label) for p in am] == [("Maria Thom", "PATIENT"), ("Monday", "DATE")]
    low = a.decode(raw, text, 0.3)                       # 1-P(O)=0.4 >= 0.3 -> whole name
    assert [(p.text, p.canonical_label) for p in low] == [("Maria Thompson", "NAME"), ("Monday", "DATE")]
    assert a.decode(raw, text, 0.95) == []


def test_bilou_decoding_merges_within_word_splits_at_new_word():
    tags = ("O", "U-ID", "B-PATIENT", "L-PATIENT", "U-PATIENT")
    a = _hf_stub("obi_roberta", tags, "BILOU")
    text = "48291 Ann Lee Bob"
    offsets = np.array([[0, 2], [2, 5], [6, 9], [10, 13], [14, 17]])
    probs = np.zeros((5, 5))
    probs[0, 1] = probs[1, 1] = 1.0          # U-ID on both sub-tokens of one word -> one span
    probs[2, 2] = probs[3, 3] = 1.0          # B-PATIENT, L-PATIENT -> one span
    probs[4, 4] = 1.0                        # U-PATIENT on a new word -> new span
    raw = RawOutput(payload={"offsets": offsets, "probs": probs}, truncation=TI)
    assert [(p.text, p.canonical_label) for p in a.decode(raw, text, None)] == [("48291", "OTHER_UNIQUE_ID"), ("Ann Lee", "NAME"), ("Bob", "NAME")]
