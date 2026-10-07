"""Download the pinned model revisions into the local Hugging Face cache.

This is the ONLY command that needs network access. It downloads public model
artifacts only (never datasets, never test data). Everything else runs with
OFFLINE_MODE=true.
"""
from __future__ import annotations

import os


def main(argv=None) -> None:
    os.environ["OFFLINE_MODE"] = "false"
    os.environ.pop("HF_HUB_OFFLINE", None)
    os.environ.pop("TRANSFORMERS_OFFLINE", None)
    from huggingface_hub import snapshot_download

    from harness.config import models_registry

    for key, e in models_registry().items():
        print(f"{key}: {e['model_id']} @ {e['revision']}", flush=True)
        snapshot_download(e["model_id"], revision=e["revision"])
        if e["adapter"] == "gliner":
            # GLiNER bi-encoders also load their backbone configs/tokenizers by name.
            import json
            from huggingface_hub import hf_hub_download
            cfg = json.load(open(hf_hub_download(e["model_id"], "gliner_config.json", revision=e["revision"])))
            for dep in {cfg.get("model_name"), cfg.get("labels_encoder")} - {None}:
                print(f"  dependency {dep}", flush=True)
                snapshot_download(dep, allow_patterns=["*.json", "*.txt", "tokenizer*", "*.model"])
    print("done - evaluation can now run with OFFLINE_MODE=true (default)")


if __name__ == "__main__":
    main()
