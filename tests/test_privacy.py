import pytest

from harness.datasets import DatasetUnavailable, load_dataset
from harness.env import offline_mode_enabled
from harness.privacy import TextPolicy


def test_offline_default(monkeypatch):
    monkeypatch.delenv("OFFLINE_MODE", raising=False)
    assert offline_mode_enabled()


def test_public_text_redacted_by_default():
    assert TextPolicy(is_synthetic=False).text("John Smith") == "[REDACTED 10 chars]"
    assert TextPolicy(is_synthetic=True).text("Maria") == "Maria"


def test_public_dataset_requires_path_and_flag(tmp_path):
    with pytest.raises(DatasetUnavailable):
        load_dataset("i2b2_2014")
    with pytest.raises(DatasetUnavailable, match="allow-local-public-dataset"):
        load_dataset("i2b2_2014", str(tmp_path), allow_public=False)


def test_i2b2_2014_parser(tmp_path):
    xml = ('<?xml version="1.0"?><deIdi2b2><TEXT><![CDATA[Seen by Dr. Ann Lee on 2/3/2090. Age 45.]]></TEXT>'
           '<TAGS><DOCTOR id="P0" start="12" end="19" text="Ann Lee" TYPE="DOCTOR"/>'
           '<DATE id="P1" start="23" end="31" text="2/3/2090" TYPE="DATE"/>'
           '<AGE id="P2" start="37" end="39" text="45" TYPE="AGE"/></TAGS></deIdi2b2>')
    (tmp_path / "100-01.xml").write_text(xml)
    ds = load_dataset("i2b2_2014", str(tmp_path), allow_public=True)
    s = ds.samples[0]
    assert [(e.text, e.label) for e in s.entities] == [("Ann Lee", "PROVIDER_NAME"), ("2/3/2090", "DATE")]
    assert len(s.ignore_spans) == 1 and not ds.is_synthetic
