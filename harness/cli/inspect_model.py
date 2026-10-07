"""Print the evidence registry entry for a model and, unless --no-load, load it locally
to confirm the architecture / parameter count / labels actually present in the checkpoint.

  python -m harness.inspect_model --model stanford_base
"""
from __future__ import annotations

import argparse
import textwrap

from harness.cli.common import setup_logging
from harness.config import all_model_keys, model_entry
from harness.label_mapping import COVERAGE_SYMBOL, LabelMapping


def _w(s) -> str:
    return textwrap.fill(" ".join(str(s).split()), 96, initial_indent="  ", subsequent_indent="  ")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m harness.inspect_model", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True, choices=all_model_keys())
    ap.add_argument("--no-load", action="store_true", help="registry only; do not load weights")
    ap.add_argument("--device", default="cpu")
    args = ap.parse_args(argv)
    setup_logging()
    e = model_entry(args.model)
    mapping = LabelMapping.load(args.model)
    measured = {}
    if not args.no_load:
        from harness.adapters import build_adapter
        a = build_adapter(args.model, args.device)
        a.load()
        measured = a.get_metadata()
        a.unload()

    print(f"Model:\n  {e['model_id']}\n")
    print(f"Revision:\n  {e['revision']}\n")
    print(f"Architecture:\n{_w(e['architecture'])}\n")
    print(f"Parameters:\n  registry {e.get('parameter_count'):,}" +
          (f" | measured on load {measured['parameter_count_measured']:,}" if measured.get("parameter_count_measured") else "") + "\n")
    print(f"Native labels ({len(e.get('native_labels') or [])}):\n  {', '.join(e.get('native_labels') or []) or 'NONE DOCUMENTED'}\n")
    if e["adapter"] == "gliner" and measured:
        print(f"Requested labels (sent to GLiNER):\n  {', '.join(measured.get('requested_labels', []))}\n")
    print(f"Label provenance:\n  {mapping.provenance}\n")
    print(f"Maximum sequence length:\n  {e['maximum_context']}\n")
    print(f"License:\n  {e['license']}\n")
    status = measured.get("status", "not loaded (--no-load)")
    print(f"Local inference:\n  {'YES' if e.get('local_inference_supported') else 'NO'}  (load status: {status}"
          + (f", error: {measured['load_error']}" if measured.get("load_error") else "") + ")\n")
    print(f"Intended inference:\n{_w(e.get('intended_inference', ''))}\n")
    print(f"Training data:\n{_w(e.get('training_data_description', ''))}\n")
    pe = e.get("published_evidence", {})
    print("Published clinical evidence (PUBLISHED - not measured by this harness):")
    print(f"  source: {pe.get('source')}")
    for r in pe.get("results") or []:
        print(f"  - {r['dataset']}: {r['metric']} = {r['value']}")
    if not pe.get("results"):
        print("  - none")
    print(f"  checkpoint match:\n{_w(pe.get('checkpoint_match', ''))}\n")
    print("Known limitations:")
    for x in e.get("known_limitations", []):
        print(f"  - {x}")
    cov = mapping.coverage()
    print("\nCanonical coverage (Y full, P partial, Z zero-shot, N none):")
    print("  " + "  ".join(f"{c}={COVERAGE_SYMBOL[v]}" for c, v in cov.items()))
    if measured.get("runtime_notes"):
        print("\nRuntime notes:\n  " + "\n  ".join(measured["runtime_notes"]))


if __name__ == "__main__":
    main()
