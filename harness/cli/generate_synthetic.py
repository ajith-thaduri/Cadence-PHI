"""Generate data/synthetic/{dev,test}.jsonl and manifest.json."""
from __future__ import annotations

import argparse
import json

from harness.cli.common import setup_logging
from harness.datagen.generate import generate_all
from harness.paths import SYNTHETIC


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m harness.generate_synthetic", description=__doc__)
    ap.add_argument("--out-dir", default=str(SYNTHETIC))
    args = ap.parse_args(argv)
    setup_logging()
    from pathlib import Path
    m = generate_all(Path(args.out_dir))
    for split, info in m["splits"].items():
        print(f"{split:5s}: {info['documents']:4d} documents, {info['entities']:5d} gold entities, sha256 {info['sha256'][:16]}")
        print("       subsets:", json.dumps(info["by_subset"]))
    print("manifest:", Path(args.out_dir) / "manifest.json")


if __name__ == "__main__":
    main()
