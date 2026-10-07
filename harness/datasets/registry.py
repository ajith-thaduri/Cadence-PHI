"""Dataset registry: name -> loader. Public datasets require an explicit local path AND
--allow-local-public-dataset; nothing is ever downloaded."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from harness.config import benchmark_config
from harness.datasets.base import Dataset
from harness.datasets.i2b2 import load_i2b2_2006, load_i2b2_2014
from harness.datasets.meddocan import load_meddocan
from harness.datasets.physionet import load_physionet_deid
from harness.datasets.synthetic import load_synthetic
from harness.paths import resolve


class DatasetUnavailable(Exception):
    """Raised when a dataset cannot be evaluated. Reported as NOT RUN - DATASET NOT AVAILABLE."""


PUBLIC_LOADERS = {"i2b2_2014": load_i2b2_2014, "i2b2_2006": load_i2b2_2006,
                  "physionet_deid": load_physionet_deid}


def load_dataset(name: str, data_path: Optional[str] = None, allow_public: bool = False) -> Dataset:
    cfg = benchmark_config()["datasets"]
    if name not in cfg:
        raise KeyError(f"unknown dataset {name!r}; known: {', '.join(cfg)}")
    spec = cfg[name]
    if spec["adapter"] == "meddocan":
        path = resolve(data_path or spec["path"])
        if not path.exists():
            raise DatasetUnavailable(f"{name}: {path} missing - run `python -m harness.download_public`")
        return load_meddocan(name, path)   # open CC BY 4.0 corpus with synthetic PHI; no DUA needed
    if spec["adapter"] == "tier3":
        from harness.datasets.base import load_jsonl, sha256_path
        path = resolve(spec["path"])
        if not path.exists():
            raise DatasetUnavailable(f"{name}: {path} missing - run `python -m harness.decide build`")
        return Dataset(name, load_jsonl(path), "realistic", str(path), sha256_path(path), True,
                       ["TIER 3: independently authored Cadence-realistic documents; fabricated PHI; NOT real PHI."])
    if spec["adapter"] == "synthetic":
        path = resolve(data_path or spec["path"])
        if not path.exists():
            raise DatasetUnavailable(f"{path} missing - run `python -m harness.generate_synthetic`")
        return load_synthetic(name, path)
    path_str = data_path or spec.get("path")
    if not path_str:
        raise DatasetUnavailable(f"{name}: no --data-path supplied (restricted dataset; never auto-downloaded)")
    path = Path(path_str).expanduser()
    if not path.exists():
        raise DatasetUnavailable(f"{name}: path {path} does not exist")
    if not allow_public:
        raise DatasetUnavailable(
            f"{name}: evaluating real clinical text requires --allow-local-public-dataset "
            "(confirms you hold a valid DUA and that the data stays on this machine)")
    return PUBLIC_LOADERS[spec["adapter"]](path)
