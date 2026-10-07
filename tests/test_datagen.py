import re

from harness.datagen.generate import generate_split
from harness.datagen.templates import TEMPLATES
from harness.taxonomy import SUBSETS


def test_deterministic_and_valid_offsets():
    a, b = generate_split("dev"), generate_split("dev")
    assert [s.model_dump() for s in a] == [s.model_dump() for s in b]
    for s in a:
        s.validate_offsets()
        assert s.source == "synthetic"


def test_dev_and_test_template_families_are_disjoint():
    for subset, fams in TEMPLATES.items():
        dev = {t for _, t, _ in fams["dev"]}
        test = {t for _, t, _ in fams["test"]}
        assert dev and test and dev.isdisjoint(test), subset
    dev_ids = {s.metadata["template_id"] for s in generate_split("dev")}
    test_ids = {s.metadata["template_id"] for s in generate_split("test")}
    assert dev_ids.isdisjoint(test_ids)


def test_all_subsets_and_noise_levels_present():
    test = generate_split("test")
    assert {s.subset for s in test} == set(SUBSETS)
    assert {s.metadata["noise_level"] for s in test} == {"clean", "low", "medium", "high"}
    assert 100 <= len(test) <= 250
    assert any(s.metadata.get("target_tokens", 0) >= 12000 for s in test)


def test_values_are_fabricated_and_natural():
    rows = generate_split("test")
    for s in rows:
        for e in s.entities:
            if e.label == "EMAIL" and "@" in e.text:
                assert re.search(r"@example\.(com|org|net)$", e.text)
            if e.label == "PHONE" and e.text.startswith("("):
                assert "555-01" in e.text
    natural = sum("natural_format" in e.tags for s in rows for e in s.entities)
    stylized = sum("stylized_format" in e.tags for s in rows for e in s.entities)
    assert natural > 5 * max(1, stylized)


def test_no_entity_glued_to_filler():
    for s in generate_split("test") + generate_split("dev"):
        for e in s.entities:
            nxt = s.text[e.end:e.end + 1]
            assert not (nxt.isalpha() and e.text[-1:].isalnum()), (s.id, e.text, s.text[e.end:e.end + 5])


def test_negative_controls_have_no_phi_and_registry_present():
    for s in generate_split("test"):
        if s.subset == "NEGATIVE_CONTROL":
            assert not s.entities
        assert "registry" in s.metadata
