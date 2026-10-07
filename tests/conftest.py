import pytest

from harness.schema import GoldEntity, IgnoreSpan, Prediction, Sample


def ent(text, sub, label, occurrence=0):
    start = -1
    for _ in range(occurrence + 1):
        start = text.index(sub, start + 1)
    return GoldEntity(start=start, end=start + len(sub), text=sub, label=label)


def pred(text, sub, native="X", canonical="NAME", score=0.9):
    s = text.index(sub)
    return Prediction(start=s, end=s + len(sub), text=sub, native_label=native, canonical_label=canonical, score=score)


def always(native, gold):
    return True


@pytest.fixture
def sample():
    text = "Maria Thompson spoke with Dr. Michael Carter at Sunrise Valley Medical Center. MRN-847291."
    return Sample(id="t1", text=text, entities=[
        ent(text, "Maria Thompson", "NAME"),
        ent(text, "Dr. Michael Carter", "PROVIDER_NAME"),
        ent(text, "Sunrise Valley Medical Center", "FACILITY_NAME"),
        ent(text, "MRN-847291", "MRN"),
    ], metadata={"subset": "CLEAN_CLINICAL", "language": "en", "noise_level": "clean", "source": "synthetic"})

# Unit / contract tests run the `test` environment of the layered configuration (configs/phi/
# environments/test.yaml): placeholder thresholds, no generated calibration overlay - so expectations
# do not move when dev calibration is re-run.
import os as _os

_os.environ.setdefault("PHI_ENV", "test")
