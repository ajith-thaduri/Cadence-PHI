"""P1 configuration architecture: layered composition, strict schema, restricted overlays, provenance,
catalog-driven behaviour (add an entity with NO code), modality thresholds, legacy equivalence."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from harness.paths import ROOT
from harness.phi.config import ConfigError, HarnessConfig
from harness.phi.contracts import DetectionRequest, Operator
from harness.phi.engine import PHIEngine
from harness.phi.recognizers.patterns import VALIDATORS
from test_phi_harness import SPANS, FakeAdapter, store

LAYERED = ROOT / "configs" / "phi"


@pytest.fixture
def tmp_layout(tmp_path):
    dst = tmp_path / "phi"
    shutil.copytree(LAYERED, dst)
    return dst


def _edit(path: Path, fn) -> None:
    data = yaml.safe_load(path.read_text())
    fn(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def eng(cfg):
    return PHIEngine(cfg, registry_store=store(), adapters={m: FakeAdapter(m, SPANS) for m in cfg.raw["models"]})


# ---------------------------------------------------------------------------- loader / schema
def test_all_environments_resolve_and_hash_differently():
    hashes = {e: HarnessConfig.load(env=e).sha256() for e in ("base", "dev", "test", "prod")}
    assert len(set(hashes.values())) == 4
    prod = HarnessConfig.load(env="prod")
    assert prod.raw["output"]["show_text"] == "never" and prod.raw["redaction"]["vault"]["backend"] == "encrypted_file"
    src = prod.raw["_source"]
    assert src["environment"] == "prod" and all(f["sha256"] for f in src["files"])


def test_unknown_key_rejected(tmp_layout):
    _edit(tmp_layout / "models.yaml", lambda d: d["models"]["obi_roberta"].update({"enabeld": True}))
    with pytest.raises(ConfigError, match="enabeld"):
        HarnessConfig.load(str(tmp_layout / "phi.yaml"))


def test_unknown_entity_reference_rejected(tmp_layout):
    _edit(tmp_layout / "policy" / "thresholds.yaml", lambda d: d["thresholds"]["entity"].update({"NAMEE": 0.5}))
    with pytest.raises(ConfigError, match="NAMEE"):
        HarnessConfig.load(str(tmp_layout / "phi.yaml"))


def test_out_of_range_threshold_rejected(tmp_layout):
    _edit(tmp_layout / "policy" / "thresholds.yaml", lambda d: d["thresholds"]["model"].update({"obi_roberta": 1.7}))
    with pytest.raises(ConfigError):
        HarnessConfig.load(str(tmp_layout / "phi.yaml"))


def test_yaml_boolean_trap_caught(tmp_layout):
    p = tmp_layout / "entities.yaml"
    p.write_text(p.read_text().replace('strip_affixes: [ssn, social, security, number, "no"]',
                                       "strip_affixes: [ssn, social, security, number, no]"))
    with pytest.raises(ConfigError):
        HarnessConfig.load(str(tmp_layout / "phi.yaml"))


def test_same_key_in_two_files_rejected(tmp_layout):
    _edit(tmp_layout / "runtime.yaml", lambda d: d.update({"allow_list": ["Cadence"]}))   # owned by exclusions.yaml
    with pytest.raises(ConfigError, match="allow_list"):
        HarnessConfig.load(str(tmp_layout / "phi.yaml"))


def test_missing_include_refuses(tmp_layout):
    (tmp_layout / "runtime.yaml").unlink()
    with pytest.raises(ConfigError, match="not found"):
        HarnessConfig.load(str(tmp_layout / "phi.yaml"))


@pytest.mark.parametrize("bad", [{"thresholds": {"model": {"obi_roberta": 0.99}}},
                                 {"entities": {"NAME": {"enabled": False}}},
                                 {"failure": {"on_model_error": "conservative_fallback"}},
                                 {"recognizers": {"rule_phone": {"enabled": False}}}])
def test_environment_overlay_cannot_change_policy(tmp_layout, bad):
    (tmp_layout / "environments" / "prod.yaml").write_text(yaml.safe_dump(bad))
    with pytest.raises(ConfigError, match="may not set"):
        HarnessConfig.load(str(tmp_layout / "phi.yaml"), env="prod")


def test_generated_overlay_requires_dev_provenance(tmp_layout):
    gen = tmp_layout / "generated" / "thresholds_calibrated.yaml"
    gen.write_text(yaml.safe_dump({"_provenance": {"developed_on": ["cadence_text"]},
                                   "thresholds": {"model": {"obi_roberta": 0.3}}}))
    with pytest.raises(ConfigError, match="non-development"):
        HarnessConfig.load(str(tmp_layout / "phi.yaml"), env="base")
    gen.write_text(yaml.safe_dump({"_provenance": {"developed_on": ["cadence_dev_text"]},
                                   "thresholds": {"model": {"obi_roberta": 0.3}, "default": 0.1}}))
    with pytest.raises(ConfigError, match="may only set"):
        HarnessConfig.load(str(tmp_layout / "phi.yaml"), env="base")
    gen.write_text(yaml.safe_dump({"_provenance": {"developed_on": ["cadence_dev_text"]},
                                   "thresholds": {"model": {"obi_roberta": 0.3}}}))
    c = HarnessConfig.load(str(tmp_layout / "phi.yaml"), env="base")
    assert c.raw["thresholds"]["model"]["obi_roberta"] == 0.3 and c.raw["_source"]["generated"]
    assert HarnessConfig.load(str(tmp_layout / "phi.yaml"), env="test").raw["thresholds"]["model"]["obi_roberta"] == 0.5


# ---------------------------------------------------------------------------- catalog-driven behaviour
def test_add_new_entity_with_no_code_change(tmp_layout):
    """A brand-new fabricated identifier type, end to end, purely by configuration."""
    _edit(tmp_layout / "entities.yaml", lambda d: d["entities"].update({"WIDGET_ID": {
        "description": "Fabricated test identifier", "category": "identifier", "risk_tier": "direct", "enabled": True,
        "default_action": "REDACT", "outbound_guard": "block"}}))
    _edit(tmp_layout / "recognizers" / "patterns.yaml", lambda d: d["recognizers"].update({"pattern_widget": {
        "type": "pattern", "enabled": True, "entity": "WIDGET_ID", "developed_on": ["unit_test"],
        "patterns": [{"name": "widget", "regex": r"\bWGT-\d{6}\b", "score": 1.0}],
        "context": {"words": ["widget"], "window": 30}}}))
    c = HarnessConfig.load(str(tmp_layout / "phi.yaml"), env="test")
    e = eng(c)
    t = "The widget number is WGT-123456, please log it."
    r = e.redact(DetectionRequest(request_id="req_w", text=t, options={"mode": "redact"}))
    assert any(d.entity_type == "WIDGET_ID" for d in r.detections)
    assert "[WIDGET_ID]" in r.output.sanitized_text                      # catalog default_action REDACT used
    _edit(tmp_layout / "entities.yaml", lambda d: d["entities"]["WIDGET_ID"].update({"enabled": False}))
    c2 = HarnessConfig.load(str(tmp_layout / "phi.yaml"), env="test")
    r2 = eng(c2).redact(DetectionRequest(request_id="req_w2", text=t, options={"mode": "redact"}))
    assert "WGT-123456" in r2.output.sanitized_text                       # disabled -> not redacted


def test_per_entity_operator_and_token_format(tmp_layout):
    _edit(tmp_layout / "policy" / "redaction.yaml", lambda d: d["redaction"].update(
        {"per_entity": {"PHONE": "REDACT"}, "token_format": "<<{ENTITY}#{N}>>"}))
    c = HarnessConfig.load(str(tmp_layout / "phi.yaml"), env="test")
    e = eng(c)
    t = "Maria Hernandez can be reached at 555-013-2244."
    r = e.redact(DetectionRequest(request_id="req_o", text=t, registry_context={"subject_context_id": "ctx_test"},
                                  options={"mode": "redact"}))
    assert "[PHONE]" in r.output.sanitized_text and "<<NAME#1>>" in r.output.sanitized_text
    assert e.restore(r.output.vault_id, "call <<NAME#1>>") == "call Maria Hernandez"


def test_modality_threshold_precedence(tmp_layout):
    _edit(tmp_layout / "policy" / "thresholds.yaml", lambda d: d["thresholds"].update(
        {"modality": {"asr": {"model_entity": {"obi_roberta": {"NAME": 0.3}}, "entity": {"DATE": 0.4}}}}))
    c = HarnessConfig.load(str(tmp_layout / "phi.yaml"), env="test")
    f = c.threshold_for
    assert f("NAME", "model_obi_roberta", "obi_roberta", "model_confidence", "asr") == \
        (0.3, "thresholds.modality.asr.model_entity.obi_roberta.NAME")
    assert f("NAME", "model_obi_roberta", "obi_roberta", "model_confidence", "note")[0] == 0.5
    assert f("DATE", "model_obi_roberta", "obi_roberta", "model_confidence", "asr")[1] == "thresholds.modality.asr.entity.DATE"
    assert f("PHONE", "rule_phone", None, "regex_match", "asr") == (0.0, "thresholds.deterministic_default")


# ---------------------------------------------------------------------------- validators
@pytest.mark.parametrize("name,good,bad", [
    ("ssn_valid", "219-09-9999", "666-12-3456"), ("ipv4_valid", "198.51.100.23", "300.1.1.1"),
    ("vin_checksum", "1HGCM82633A004352", "1HGCM82633A004353"), ("npi_luhn", "1234567893", "1234567890"),
    ("age_over_89", "93", "45"), ("luhn", "4111111111111111", "4111111111111112")])
def test_named_validators(name, good, bad):
    assert VALIDATORS[name](good) and not VALIDATORS[name](bad)


# ---------------------------------------------------------------------------- legacy equivalence
def test_layered_config_equivalent_to_legacy_on_dev_corpus():
    """The migration must not change behaviour: legacy single file vs layered config (test env, no
    generated overlay; new entities disabled) produce identical detections on the dev corpus."""
    from harness_eval.datasets import load
    legacy = HarnessConfig.load("configs/phi_harness.yaml")
    layered = HarnessConfig.load(env="test")
    # features added AFTER the migration exist only in the layered config; switch them off to compare like for like
    layered.raw["reconciliation"], layered.raw["contextual_type_rules"] = {}, []
    layered.raw["type_refinements"] = legacy.raw.get("type_refinements", {})
    reqs, golds, st, _ = load("cadence_dev_text", languages=["en"])
    e_old = PHIEngine(legacy, registry_store=st, adapters={m: FakeAdapter(m, []) for m in legacy.raw["models"]})
    e_new = PHIEngine(layered, registry_store=st, adapters={m: FakeAdapter(m, []) for m in layered.raw["models"]})
    norm = lambda r: [(d.original_start, d.original_end, d.entity_type, d.decision) for d in r.detections]
    for q in reqs[:20]:
        a, b = e_old.analyze(q), e_new.analyze(q)
        assert a.status == b.status == "ok"
        assert norm(a) == norm(b), q.request_id
