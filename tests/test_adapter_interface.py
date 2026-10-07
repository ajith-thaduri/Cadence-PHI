from typing import Optional

from harness.adapters.base import BaseModelAdapter, ModelStatus, RawOutput
from harness.adapters.factory import ADAPTERS, build_adapter
from harness.schema import Prediction, TruncationInfo


class Dummy(BaseModelAdapter):
    fail = False

    @property
    def default_threshold(self) -> Optional[float]:
        return 0.5

    def max_input_length(self):
        return 100, "words"

    def _load(self):
        if self.fail:
            raise OSError("weights missing")

    def infer_raw(self, text, threshold=None):
        return RawOutput(payload=[(0, 5, 0.7), (6, 9, 0.2)],
                         truncation=TruncationInfo(strategy="none", max_supported=100, unit="words",
                                                   input_length=len(text.split()), truncated=False))

    def decode(self, raw, text, threshold):
        t = threshold or 0
        return [Prediction(start=s, end=e, text=text[s:e], native_label="person",
                           canonical_label=self.mapping.to_canonical("person"), score=sc)
                for s, e, sc in raw.payload if sc >= t]


def test_interface_methods():
    a = Dummy("mednotes_gliner", "cpu")
    assert a.load() and a.status == ModelStatus.LOADED
    preds = a.predict("Maria Tom x")
    assert [p.text for p in preds] == ["Maria"] and preds[0].canonical_label == "NAME"
    assert len(a.predict_batch(["Maria Tom x", "Maria Tom y"])) == 2
    assert len(a.predict("Maria Tom x", threshold=0.1)) == 2
    md = a.get_metadata()
    for k in ("model_id", "revision", "native_labels", "status", "max_input_length", "default_threshold"):
        assert k in md
    assert "person" in a.get_native_labels()


def test_load_failure_is_recorded_not_raised():
    a = Dummy("mednotes_gliner", "cpu")
    a.fail = True
    assert a.load() is False
    assert a.status == ModelStatus.FAILED_TO_LOAD and "weights missing" in a.load_error


def test_factory_has_all_four():
    assert set(ADAPTERS) == {"stanford_base", "stanford_v2", "logical_gliner", "mednotes_gliner"}
    a = build_adapter("stanford_base", "cpu")
    assert a.default_threshold is None and a.max_input_length() == (512, "subword_tokens")
    from harness.config import default_model_keys
    keys = default_model_keys()
    assert "logical_gliner" not in keys and "kindlab_roberta" not in keys and "obi_roberta" in keys
    for k in keys:
        build_adapter(k, "cpu")   # every configured model constructs (labels/mappings consistent)
