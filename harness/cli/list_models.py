"""List the registered model checkpoints (no network, no loading)."""
from __future__ import annotations

from harness.config import models_registry


def main(argv=None) -> None:
    print(f"{'KEY':18s} {'MODEL ID':55s} {'REVISION':10s} ADAPTER")
    for k, e in models_registry().items():
        print(f"{k:18s} {e['model_id']:55s} {e['revision'][:8]:10s} {e['adapter']}")


if __name__ == "__main__":
    main()
