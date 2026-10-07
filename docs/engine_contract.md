# PHI engine contract (v1.0)

> **Scope this phase: English only.** `languages.supported: [en, en-US]` in the default profile.
> Requests in other languages raise `UnsupportedLanguageError`, and only English Presidio
> recognizer instances are built. The only production model is OBI RoBERTa i2b2 (pinned revision
> `78f2152eb93ddd817290ce8dbe46f1a6685e09fc`). Stanford V2 and MedNotes GLiNER are optional
> experimental adapters, disabled by default.

The types are defined in `harness/phi/contracts.py` (Pydantic, `extra="forbid"`). The same objects
are used by the Python API, the CLI, JSONL batch files, the tests and any future service wrapper.

```
DetectionRequest ──► PHIEngine.analyze() / .redact() ──► DetectionResponse
                                          │
                                          └─► (REDACT) TokenVault, kept inside the engine, never in a response
GoldDocument + DetectionResponse ──► evaluation ──► EvaluationRecord        (evaluation only)
```

## Minimum application API

```python
from harness.phi.config import HarnessConfig
from harness.phi.contracts import DetectionRequest
from harness.phi.engine import PHIEngine

engine = PHIEngine(HarnessConfig.load("configs/phi_harness.yaml"), registry_store=store)
response = engine.analyze(DetectionRequest(request_id="req_...", text=transcript, language="en",
                                           modality="telephone", normalization_profile="asr",
                                           registry_context={"subject_context_id": "ctx_..."}))
redaction = engine.redact(RedactionRequest(request_id=..., original_text=text,
                                           detections=response.detections))   # -> RedactionResponse (+ vault kept inside)
response2 = engine.redact(request)            # convenience: analyze + redact -> DetectionResponse with output.sanitized_text
answer = engine.restore(redaction.vault_id, bedrock_answer)   # protected environment only; unknown vault_id -> KeyError
```

Applications never call OBI, the registry or a regex directly. Those sit behind the engine. See
`examples/analyze_example.py`, `examples/request.json` and `examples/response.json` (synthetic).

## 1. DetectionRequest

| field | meaning |
|---|---|
| `request_id` | Opaque id. It is refused if it looks like an identifier (≥6 digits). Never use a member ID. |
| `text` (alias `original_text`) | The original source text. This is **the** coordinate space. It is never mutated. |
| `language` | One of `en`, `es`, `en-US`, `es-US`, `es-ES`, `es-MX`, `mixed`, `auto`. Mapped to a recognizer language by `languages.map`. |
| `modality` | One of `typed`, `note`, `dictation`, `telephone`, `face_to_face`, `asr`, `imported_text`. It is descriptive only: behaviour comes from the profiles, never from modality. |
| `source` | Optional descriptor, e.g. `cadence_call`. |
| `normalization_profile` | A named list of normalizers: `none`, `default`, `asr`, `spanish_asr`, `aggressive`. |
| `enabled_entities` / `enabled_recognizers` / `enabled_models` | Optional allow-lists. They can only narrow what the config enables. `null` means config defaults. Models have their own list. |
| `registry_context` | Opaque references only: `subject_context_id` and `provider_/facility_/staff_directory_version`. Raw records are rejected at validation. |
| `policy_profile` | `cadence_default`, `evaluation`, `research`, or a custom profile in the YAML. |
| `metadata` | Optional flat session/request metadata (`str`, `int`, `float`, `bool` values only). Must not hold PHI. Never logged. |
| `options` | `mode` (`detect`, `redact`, `debug`, `score`), `include_candidates`, `include_rejected`, `include_trace`, `include_text`, `strict_logging`, `redaction_operator`. `debug` turns on candidates, rejected evidence and trace. |

## 2. Coordinates and text views

- Every span leaving the engine (`DetectionEvidence`, `Detection`, `Replacement`) is in
  **original-text characters, `[start, end)`**.
- Normalizers build a derived `TextView` with an `OffsetSegment` map
  (`derived_start/end → original_start/end`, `transformation_id`). The request text is never
  touched.
- `TextView.to_original()` maps a derived span back to the original:
  - identity segments map character by character;
  - a span touching a rewritten segment **snaps outward** to that segment's whole original extent.

  For example, "555" found inside "5550122" maps to all of "five five five, oh one two two". A
  redaction therefore always covers the original words that produced the hit.
- `mapping_quality`:
  - `identity`;
  - `exact_segment` (all current normalizers);
  - `approximate`. Evidence from an approximate view is rejected for final redaction
    (`rejection_reason = approximate_mapping`) and kept only as supporting evidence.
- Each normalizer emits a `NormalizationTrace`: `normalizer_id`, `version`, input/output text
  sha256, transformation count and mapping quality. The response lists `text_views` with ids,
  transformations and quality, but never the derived text.

## 3. Recognizers and evidence

**The one recognizer contract:**
`PHIRecognizer.recognize(ctx, view) -> list[DetectionEvidence]`.
- Every recognizer is a Presidio `EntityRecognizer`; Presidio's registry selects it and calls it.
- Recognizers only propose evidence. They never threshold, reconcile, redact or modify text.

**`DetectionEvidence` fields:**

| group | fields |
|---|---|
| identity and span | `evidence_id` (deterministic), `entity_type`, `original_start/end` |
| score | `raw_score`, `score_type` |
| provenance | `recognizer_id/version`, `model_id/revision`, `rule_id`, `source`, `text_view_id` |
| set by policy | `threshold_used`, `threshold_source`, `accepted_as_candidate`, `rejection_reason` |
| optional | `text` (only with `include_text`), `metadata` |

**`score_type`:**
- `model_confidence`
- `registry_exact`, `registry_normalized`, `registry_fuzzy`
- `regex_match`, `checksum_valid` (NPI Luhn)
- `contextual_rule`
- `spoken_number_match` (a regex hit that went through a spoken-number normalizer)

`raw_score` keeps the recognizer's own scale and is **not** a probability. No score is converted
into a common probability space.

**Registry evidence** carries `metadata.match_type` (exact, normalized or fuzzy),
`metadata.registry_type` (member, provider, staff, facility, employer, contact, identifier or
record) and `metadata.registry_version`.

**Model evidence** (`ModelRecognizerAdapter`) carries `model_id`, `model_revision` and
`metadata.native_label`. Adapter metadata exposes `model_type`, `supported_entities`,
`tokenizer`, `input_limit` and `runtime_device`.

## 4. Policy: thresholds, reconciliation, decisions

**Thresholds** (`configs/phi_harness.yaml: thresholds`). The most specific setting wins, and the
key that won is stored on the evidence as `threshold_source`.
- `model_confidence` evidence: `model_entity[m][E] > entity[E] > model[m] > recognizer[r] > default`.
- Deterministic and registry evidence: `recognizer[r] > deterministic_default (0.0)`. Entity and
  model thresholds never silently drop a regex or registry hit.

**Pipeline:** candidates (evidence) → `policy.annotate` (allow-lists + thresholds) →
`reconciliation.reconcile` (overlap groups) → `policy.decide`.

**Reconciliation** (`overlap-union-wordexpand 1.0`):
- Accepted evidence is expanded to whole words. Overlapping accepted spans form one group whose
  extent is their **union** (recall first).
- Rejected evidence is attached to the overlapping group. Rejected evidence that overlaps no
  group forms KEEP or REVIEW groups.

**Allow-list.** `allow_list` in the YAML is empty by default. Evidence whose span text exactly
matches a configured non-PHI term (case-insensitive) is rejected with reason `allow_listed`. A
typical entry is the organisation's own name. The evidence stays visible in the chain.

**Entity type of a group:**
- Chosen by `precedence` over score types:
  `registry_exact > checksum_valid > registry_normalized > regex_match > spoken_number_match > contextual_rule > registry_fuzzy > model_confidence`.
- Ties go to the higher `raw_score`, then the longer span.
- `type_refinements` then let a more specific subtype win (DATE→DOB, NAME→RELATIVE_NAME,
  PROVIDER_NAME or CARE_TEAM_MEMBER), but only when the subtype evidence covers the whole selected
  span. A contact's surname alone never relabels the member's full name.

**Decision:**
- `REDACT`: at least one accepted evidence item.
- `REVIEW`: only if the policy profile sets `review_band > 0` and the best model evidence is within
  the band below its threshold. REVIEW is redacted when `redact_review: true`.
- `KEEP`: only below-threshold evidence. KEEP detections appear in DEBUG and SCORE responses.

**`confidence_class`:**
- `high`: registry_exact, checksum_valid or registry_normalized evidence, or ≥2 independent
  sources.
- `medium`: one deterministic or contextual source, or a model score ≥ threshold + 0.2.
- `low`: otherwise.

`decision_score` is `policy_aggregate`, the selected evidence's raw score. It is **not** a
probability.

## 5. DetectionResponse

| field | content |
|---|---|
| `request_id`, `engine_version`, `policy_version`, `config_sha256`, `contract_version` | |
| `input` | `text_sha256`, `text_length`, `language`, `resolved_language`, `modality` (no text) |
| `normalization` | `profile`, `normalizers_applied`, `traces`, `text_views`, `mapping_status` |
| `detections` | `Detection[]`: id, entity, original span, decision, `redaction_action`, `decision_score(+type)`, `threshold`, `confidence_class`, `decision_reason`, `contributing_evidence_ids`, `contributing_recognizers`, `rejected_evidence[]`, `policy_version`, `reconciliation_version`, `text` (only with `include_text`) |
| `evidence` | `DetectionEvidence[]`, only with `include_candidates` / `include_rejected` |
| `output` | `mode`, `sanitized_text`, `tokenized_text`, `operator`, `vault_id` |
| `summary` | `entities_detected`, `entities_by_type`, `decisions`, `recognizers_used`, `recognizers_disabled`, `models_used`, candidate counts (total, accepted, rejected, overlap groups) |
| `trace` | Optional per-stage record (recognizer, ms, evidence count). Never text. |
| `timings` | `normalization_ms`, `recognizer_ms`, `model_ms`, `reconciliation_ms`, `policy_ms`, `redaction_ms`, `total_ms`, `per_recognizer_ms` |

**Production defaults** (`mode=detect`, no include flags) return no span text and no evidence list.
Logs carry only request id, text hash prefix, counts and timings.

**Modes share one code path:**
- `debug`: everything, plus span text when `include_text`. Local only.
- `score`: the same engine; scoring happens outside it.
- `redact`: adds the output block.

## 6. Redaction

`RedactionRequest(original_text, detections, operator, operators_by_entity, redact_review)` →
`RedactionResponse(sanitized_text, replacements[], vault_id, original_sha256)`, plus a separate
`TokenVault`.

| operator | output | reversible? |
|---|---|---|
| `REDACT` | `[NAME]` | no |
| `TOKENIZE` (Cadence default) | `[NAME_1]`, consistent per document | via the vault |
| `PSEUDONYMIZE` | `NAME-3f9a2c`, keyed HMAC (`PHI_PSEUDONYM_KEY`) | via the vault |
| `ENCRYPT` | Presidio AES; key in `PHI_ENCRYPTION_KEY` | with the key |

- Redaction runs on reconciled, non-overlapping original offsets and refuses overlapping input.
- Presidio's AnonymizerEngine applies the operators, with one operator config per detection.
- Each `Replacement` records both original and output offsets.
- The token map never appears in a response, the sanitized text or a log.

### 6a. Vault, consistency scope and re-identification

- **Consistency scope.** `redaction.consistency_scope` decides which requests share token numbering:
  - `document`: each request starts fresh.
  - `conversation`: requests with the same `metadata.conversation_id` share one vault, so the same surface keeps
    the same token across turns.
  - `member`: requests with the same `registry_context.subject_context_id` share one vault.

  A request without the scope key falls back to `document` scope; this is counted, not an error.
- **Vault backends.** `memory` is bounded LRU with TTL. `encrypted_file` uses AES-256-GCM, one file per vault,
  with the key from `key_ref`. If the key is missing, `EngineStartupError` is raised.
- **Restore.** `engine.restore(vault_id, downstream_text, actor=..., purpose=...)` replaces only tokens issued by
  that vault. It is audited and raises `KeyError` for an unknown or expired vault.
- **Vault failures fail closed.** A store that is unavailable or fails integrity checks raises
  `EngineFailure(vault_unavailable)`, and the request is blocked.

### 6b. Outbound guard (before any egress)

```python
guard = OutboundGuard(cfg, registry_store)
res = guard.check(sanitized_text, issued_tokens=list(vault.tokens), registry_context=req.registry_context)
if not res.allowed:   # leaked direct identifier, or the guard itself failed
    ...               # do NOT send
```

- **Independent of the main engine.** The guard is a separate engine built from the same config with every model
  disabled. It ignores the engine's own tokens.
- **Blocking.** It blocks on entities whose catalog `outbound_guard` is `block`.
- **Safe to log.** Hits carry entity type, offsets and recognizer IDs, never text.

### 6c. Shadow mode and monitoring

- **Shadow mode.** A model or recognizer with `mode: shadow` runs, but its evidence is rejected with
  `shadow_mode`. It never changes a decision or the output. `summary.shadow_only_spans` counts spans the shadow
  would have added.
- **Canaries.** `harness.phi.monitoring.run_canaries(engine)` runs the configured canaries. Requests carry
  `metadata.canary=true` and are excluded from the detection-rate counters.
- **Rate checks.** `check_rates(baseline, telemetry.snapshot())` raises Poisson control-chart alerts per
  entity × modality.

## 7. Evaluation (separate from production)

**Gold.** `GoldDocument(request_id, dataset, entities: GoldEntity[], asr_lost[])`.
- `GoldEntity`: `annotation_id`, `entity_type`, `start`, `end`, `source`, `annotation_version`,
  `ignore_region`, `edge_policy` (`exact` or `trim_edge_function_words`, which is refinement D3),
  `tags`.
- Gold is never part of a request.

**Records.** `EvaluationRecord` references a response and holds:
- TP/FN/FP counts, residual characters and over-redacted characters;
- an `EntityOutcome[]` for every gold entity.

**Outcome attribution:**

| outcome | meaning |
|---|---|
| `detected_by_rule`, `detected_by_registry`, `detected_by_ml`, `detected_by_multiple_sources` | fully redacted, attributed to the sources that accepted it |
| `partial_span` | redacted, but some core characters remain visible |
| `below_threshold` | proposed with a score below threshold (best rejected score and threshold recorded) |
| `policy_rejected` | entity disabled by config or request |
| `disabled_only` | seen only by switched-off recognizers, models or normalizers |
| `no_candidate` | nothing proposed it |
| `reconciliation_failure` | accepted evidence overlapped it but no final detection did (expected 0) |
| `ASR_destroyed` | deleted or garbled by ASR; never in the text |

ASR failure is distinguished from NER failure by comparing the clean-text and ASR versions of the
same documents. The ASR dataset build aligns gold through the transcript, and entities that do not
survive are recorded as `asr_lost`.

**Batch files.** The canonical interchange is JSONL: one `DetectionRequest` per line
(`harness.phi.export`) in, and one `DetectionResponse` per line (`predictions.jsonl`) out. A run
also writes:
- `detections.jsonl`, `evidence.jsonl`, `evaluation_records.jsonl`
- `summary.json`
- `entity_metrics.csv`, `document_metrics.csv`, `residual_phi.csv`, `false_positives.csv`,
  `false_negatives.csv`, `thresholds.csv`, `attribution.csv`
- `report.html` and `report.md`, which are derived files

Span text appears in outputs only for fabricated data (`output.show_text: auto`).

**Model cache.** Loaded model weights are cached per process, keyed by model and device
(`harness.phi.engine._ADAPTER_CACHE`). Repeated requests, and repeated `PHIEngine` instances, never
reload weights.

**Manual evaluation.** `python -m harness.phi.analyze` runs files, directories or JSONL through the
same engine calls, with optional redaction and optional gold. See `docs/manual_evaluation.md`.

## 8. Configuration and extension

- **Entities** (`entities.<E>.enabled`), **recognizers** (`recognizers.<id>.enabled`), **models**
  (`models.<id>.enabled`) and **normalizers** (`normalizers.<id>.enabled`) are each one YAML
  flag. They can also be switched per request (allow-lists) or from the CLI
  (`--enable/--disable/--entities/--model/--preset/--set`).
- **Presets** for ablations (`RULES_ONLY`, `OBI_FULL`, `OBI_STANFORD_FULL`, …) are configuration,
  never code.
- **Calibration:** `python -m harness.calibrate --dataset cadence_dev` refuses test data and writes
  an overlay (`thresholds.model` and `thresholds.model_entity`) plus a reliability report. It
  applies no Platt or isotonic scaling. Apply the overlay with `--thresholds`.
- **Adding a model:**
  1. Add an entry to `configs/models.yaml` (adapter type, pinned revision, label mapping file).
  2. Add `models.<id>` to `configs/phi_harness.yaml`.

  The engine wraps it in a `ModelRecognizerAdapter`, and evaluation, calibration and comparison
  pick it up with no code change.
- **Entity responsibility matrix:** `python -m harness.phi.matrix` generates
  `configs/entity_responsibility.yaml` and `docs/entity_responsibility_matrix.md`.
