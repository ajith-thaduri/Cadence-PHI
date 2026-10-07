"""Cadence PHI detection engine (contract v1.0 - docs/engine_contract.md).

    DetectionRequest -> PHIEngine.analyze() / .redact() -> DetectionResponse

  contracts.py      canonical Pydantic types (request, evidence, detection, response, redaction, gold, evaluation)
  engine.py         the only application entry point; Presidio-orchestrated evidence collection
  normalizers/      derived text views with exact offset maps (never mutate the source)
  recognizers/      rules, contextual rules, scoped registry, ModelRecognizerAdapter, Presidio predefined
  reconciliation/   overlap grouping (union, word expansion)
  policy/           thresholds, precedence, REDACT/KEEP/REVIEW, confidence class
  redaction/        operators + TokenVault (separate from detection)
  registry_store.py scoped resolution of opaque RegistryContext references
  datasets.py / evaluate.py / calibrate.py / compare.py / matrix.py   evaluation-only tooling
"""
