import pytest

from harness.config import all_model_keys
from harness.label_mapping import LabelMapping
from harness.taxonomy import CANONICAL_LABELS, UNMAPPED


@pytest.mark.parametrize("key", all_model_keys())
def test_mappings_load_and_are_canonical(key):
    m = LabelMapping.load(key)
    assert m.native_labels
    for native in m.native_labels:
        c = m.to_canonical(native)
        assert c == UNMAPPED or c in CANONICAL_LABELS


def test_stanford_has_no_address_label():
    cov = LabelMapping.load("stanford_base").coverage()
    assert cov["ADDRESS"] == "none" and cov["EMAIL"] == "none"
    assert cov["PHONE"] == "full" and cov["NPI"] == "partial"


def test_v2_age_is_unmapped_not_forced():
    m = LabelMapping.load("stanford_v2")
    assert m.to_canonical("AGE") == UNMAPPED
    assert "AGE" in m.unmapped_native_labels()


def test_zero_shot_provenance_never_reports_full():
    for k in ("logical_gliner", "kg_stream_pii"):
        cov = LabelMapping.load(k).coverage()
        assert "full" not in cov.values() and "partial" not in cov.values()
        assert cov["NAME"] == "zero_shot"
    kg = LabelMapping.load("kg_pii_large").coverage()
    assert kg["DATE"] == "zero_shot" and kg["DOB"] == "full"     # per-label provenance


def test_label_normalization_compatibility():
    m = LabelMapping.load("mednotes_gliner")
    assert m.compatible("person", "PROVIDER_NAME")
    assert m.compatible("company", "FACILITY_NAME")
    assert not m.compatible("dates", "PHONE")
    assert m.to_canonical("not-a-label") == UNMAPPED
