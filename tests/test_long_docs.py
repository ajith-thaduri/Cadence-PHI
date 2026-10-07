from harness.adapters.chunking import word_spans, word_windows
from harness.adapters.gliner_adapter import GLiNERAdapter
from harness.adapters.hf_token_classifier import HFTokenClassificationAdapter
from harness.label_mapping import LabelMapping


def test_word_windows_overlap_and_coverage():
    w = word_windows(4500, 2000, 25)
    assert w[0] == (0, 2000) and w[1][0] == 1975 and w[-1][1] == 4500
    assert word_windows(10, 2000, 25) == [(0, 10)]


def test_word_spans_match_gliner_splitter():
    from gliner.data_processing.tokenizer import WhitespaceTokenSplitter
    text = "Dr. Carter's MRN-847291, call (555) 410-2298 re: f/u."
    theirs = [(s, e) for _, s, e in WhitespaceTokenSplitter()(text)]
    assert word_spans(text) == theirs


def _gliner(strategy):
    a = GLiNERAdapter.__new__(GLiNERAdapter)
    a.mapping = LabelMapping.load("mednotes_gliner")
    a.gcfg = {"max_length": 10, "aggregation": {"chunk_words": 8, "overlap_words": 2}, "labels": ["person"]}
    a.long_text_strategy = strategy
    a.filterable = True
    a.calls = []

    def fake_run(texts, threshold=None):
        a.calls.append(texts)
        return [[{"start": 0, "end": 4, "label": "person", "score": 0.9}] for _ in texts]
    a._run = fake_run
    return a


def test_gliner_truncation_is_reported_not_silent():
    text = " ".join(f"w{i}" for i in range(15))
    r = _gliner("truncate").infer_raw(text)
    assert r.truncation.truncated and r.truncation.input_length == 15
    assert r.truncation.visible_char_end == word_spans(text)[9][1]


def test_gliner_chunking_offsets_are_global():
    text = " ".join(f"w{i:02d}" for i in range(20))
    a = _gliner("author")
    r = a.infer_raw(text)
    assert not r.truncation.truncated and r.truncation.num_chunks == len(a.calls[0])
    starts = sorted(e["start"] for e in r.payload)
    assert starts[0] == 0 and all(text[s:s + 4].startswith("w") for s in starts)


def _hf(strategy):
    a = HFTokenClassificationAdapter.__new__(HFTokenClassificationAdapter)
    a._break_ids = {99}
    a.entry = {"inference": {"max_tokens": 512, "author": {"mode": "break_tokens", "window": 508},
                             "production": {"window": 256, "context": 64}}}
    a.long_text_strategy = strategy
    return a


def test_hf_windows_all_strategies():
    ids = [1] * 1200
    ids[400] = 99
    words = list(range(1200))
    w = _hf("author")._windows(ids, words)
    assert (w[0][1], w[0][2]) == (0, 401) and w[-1][2] == 1200 and all(ke - ks <= 508 for _, ks, ke, _ in w)
    assert _hf("truncate")._windows(ids, words) == [(0, 0, 510, 510)]
    nv = _hf("naive")._windows(ids, words)
    assert [x[1] for x in nv] == [0, 510, 1020] and all(x[0] == x[1] for x in nv)
    pr = _hf("production")._windows(ids, words)
    cores = [(ks, ke) for _, ks, ke, _ in pr]
    assert cores[0] == (0, 256) and cores[-1][1] == 1200
    assert all(ce - cs <= 510 for cs, _, _, ce in pr)          # core + context fits the model window
    assert pr[1][0] == 256 - 64 and pr[0][3] == 256 + 64        # context on both sides
