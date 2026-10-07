"""Minimal application usage of the PHI engine (synthetic example; runs locally/offline).

    OFFLINE_MODE=true python examples/analyze_example.py

An application builds a DetectionRequest and calls engine.analyze() / engine.redact().
It never calls OBI, the registry or any regex directly - those live behind the engine.
"""
from pathlib import Path

from harness.phi.config import HarnessConfig
from harness.phi.contracts import DetectionOptions, DetectionRequest, Mode, Operator, RedactionRequest
from harness.phi.engine import PHIEngine
from harness.phi.registry_store import JsonRegistryStore

HERE = Path(__file__).parent

cfg = HarnessConfig.load("configs/phi_harness.yaml")          # default production profile: English, OBI only
engine = PHIEngine(cfg, device="cpu", registry_store=JsonRegistryStore(HERE / "registry"))

request = DetectionRequest.model_validate_json((HERE / "request.json").read_text())

# DEBUG: full evidence chain (local development only - includes span text)
response = engine.analyze(request)
(HERE / "response.json").write_text(response.model_dump_json(indent=2))
for d in response.detections:
    print(f"{d.decision:<6} {d.entity_type:<14} [{d.original_start}:{d.original_end}] {d.text!r:<44} {d.confidence_class:<6} {d.decision_reason}")

# production-shaped flow: analyze with production defaults (no span text, no evidence list) ...
prod = request.model_copy(update={"options": DetectionOptions(mode=Mode.DETECT)})
detected = engine.analyze(prod)
# ... then redact as a SEPARATE operation on the final detections
redaction = engine.redact(RedactionRequest(request_id=prod.request_id, original_text=prod.text,
                                           detections=detected.detections, operator=Operator.TOKENIZE))
print("\n" + redaction.sanitized_text)
(HERE / "response_redact.json").write_text(redaction.model_dump_json(indent=2))

# inside the protected environment only: re-identify tokens in a downstream (e.g. Bedrock) answer
answer = "Please call [NAME_1] at [PHONE_1] to confirm the appointment with [PROVIDER_NAME_1]."
print("\n" + engine.restore(redaction.vault_id, answer))
