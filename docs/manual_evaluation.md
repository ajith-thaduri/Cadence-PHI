# Manual evaluation of the PHI engine (English)

`python -m harness.phi.analyze` runs your text through **the same `PHIEngine` an application
uses**, via the same calls: `engine.analyze(DetectionRequest)` and then
`engine.redact(RedactionRequest)`. Gold annotations are optional. When you supply them, they are
scored in a separate evaluation layer and never enter the engine.

Scope for this phase is **English only**. The default profile refuses requests in other languages
(`UnsupportedLanguageError`). OBI RoBERTa is the only enabled model, and everything runs locally.

## 1. Prepare the inputs

Put documents in a directory. Subdirectories are allowed, and the `doc_id` is the path relative to
the directory, without its extension.

```
data/manual_eval/
  call_001.txt              # one document (UTF-8 plain text, any length)
  call_001.meta.json        # optional sidecar, see below
  notes/note_002.txt
  batch.jsonl               # optional: one DetectionRequest JSON per line
  registry/                 # optional registry store (never treated as a document)
    subjects/ctx_manual_001.json
    provider_directory/providers_2026_10.json
  gold.jsonl                # optional gold annotations
```

**Sidecar `<stem>.meta.json`** (all fields optional):

```json
{"subject_context_id": "ctx_manual_001", "provider_directory_version": "providers_2026_10",
 "modality": "asr", "language": "en", "normalization_profile": "asr", "policy_profile": "cadence_default",
 "metadata": {"session": "s-17"}}
```

Defaults when no sidecar is given:

| setting | default | override |
|---|---|---|
| modality | `imported_text` | `--modality` |
| normalization | `asr` (safe exact-mapping normalizers) | `--normalization-profile none\|default\|asr\|aggressive` |
| registry subject | none | `--subject ctx_manual_001` |

**Registry store** (`--registry DIR`) holds local files only:
- `subjects/<id>.json` uses the registry record format. An example is
  `data/manual_eval_example/registry/subjects/ctx_manual_001.json`.
- `<kind>/<version>.json` holds directories, where kind is `provider_directory`,
  `facility_directory` or `staff_directory`.

A request only ever names a `subject_context_id`. The engine resolves it, scoped to that one
subject, and registry contents never appear in outputs or logs.

A runnable fabricated example is in `data/manual_eval_example/`.

## 2. One document

```bash
OFFLINE_MODE=true python -m harness.phi.analyze --input sample.txt
OFFLINE_MODE=true python -m harness.phi.analyze --input data/manual_eval_example/call_001.txt \
       --registry data/manual_eval_example/registry
```

For up to three documents with no `--output`, results are printed to the console. For each
detection the printout shows:
- the decision, entity type, original-text span `[start:end)`, text, confidence class, score and
  threshold;
- every contributing evidence item (`+` recognizer, entity, raw score, score type, threshold and
  the config key that supplied it);
- every rejected evidence item (`-` recognizer, raw score, reason).

## 3. A batch (directory or JSONL)

```bash
OFFLINE_MODE=true python -m harness.phi.analyze --input-dir data/manual_eval --registry data/manual_eval/registry \
       --output results/manual_eval/run1
OFFLINE_MODE=true python -m harness.phi.analyze --input requests.jsonl --output results/manual_eval/batch
```

Outputs in `--output`:

| file | content |
|---|---|
| `report.html` / `report.md` | **Start here.** Per document: the input with detections marked (⟦TYPE: span⟧ / highlighted; hover in HTML for score and reason), a detection table (span, text, entity, decision, confidence, score, threshold, recognizers / evidence, reason), the redacted text, counts and processing time |
| `responses.jsonl` | one full `DetectionResponse` per document (DEBUG mode: evidence chain, rejected candidates, trace, timings) |
| `detections.csv` | one row per final detection (REDACT / REVIEW / KEEP) with evidence and rejected evidence |
| `evidence.csv` | every candidate from every recognizer: raw score, score type, model id/revision, native label, view / normalizer transformations, threshold, threshold source, accepted or rejection reason |
| `summary.json` | config sha, per-document counts, text hash, timings, vault ids |

## 4. Redaction

```bash
OFFLINE_MODE=true python -m harness.phi.analyze --input sample.txt --redact --output results/example
OFFLINE_MODE=true python -m harness.phi.analyze --input-dir data/manual_eval --redact --operator REDACT --output results/me_redact
```

- Redaction is a separate call on the final detections, `engine.redact(RedactionRequest(...))`,
  and it operates on original-text offsets.
- Operators:
  - `TOKENIZE` (default): `[NAME_1]`, reversible inside the engine;
  - `REDACT`: `[NAME]`;
  - `PSEUDONYMIZE`: needs `PHI_PSEUDONYM_KEY`;
  - `ENCRYPT`: needs `PHI_ENCRYPTION_KEY`.
- The sanitized text goes to `redacted/<doc_id>.txt` and into the report.
- The token map stays in the engine's in-memory TokenVault. It is never written to disk or
  printed. Only the `vault_id` is recorded.

## 5. Optional gold annotations

`gold.jsonl` has one line per document:

```json
{"doc_id": "call_001",
 "entities": [{"entity_type": "NAME", "start": 70, "end": 84, "text": "Walter Brennan"},
              {"entity_type": "PHONE", "start": 239, "end": 287}],
 "ignore": [{"start": 0, "end": 3}],
 "asr_lost": [{"entity_type": "MEMBER_ID", "kind": "deleted"}]}
```

- **Offsets** are original-text characters, `[start, end)`.
- **`text`** is optional. When present, it is checked against the document, so off-by-one
  annotations fail loudly.
- **`entity_type`** must be a canonical label (`harness/taxonomy.py`): NAME, RELATIVE_NAME,
  PROVIDER_NAME, CARE_TEAM_MEMBER, FACILITY_NAME, ADDRESS, CITY, STATE, ZIP, GEOGRAPHIC_CONTEXT,
  DATE, DOB, PHONE, EMAIL, URL, MEMBER_ID, MRN, ACCOUNT_ID, NPI, OTHER_UNIQUE_ID, EMPLOYER,
  ORGANIZATION, LANGUAGE.
- **`ignore`** marks regions where a redaction counts as neither right nor wrong.
- **`asr_lost`** lists PHI that was spoken but is absent from the transcript (see §8).
- **Edge words:** by default, lowercase articles and prepositions at span edges ("the" in "the
  twenty-second") are not counted as PHI. Use `--gold-edge-policy exact` to score spans
  verbatim.

```bash
OFFLINE_MODE=true python -m harness.phi.analyze --input-dir data/manual_eval_example \
       --registry data/manual_eval_example/registry --gold data/manual_eval_example/gold.jsonl \
       --redact --output results/manual_eval/example
```

Additional outputs with `--gold`:

| file | content |
|---|---|
| `gold_summary.json` | span precision, entity recall (any overlap and full span), high-risk full recall, residual PHI characters (mean, P95, corpus), over-redaction, spurious spans, failure attribution |
| `entity_metrics.csv` | per entity type: support, TP, FN, FP, precision, recall, full-span recall, character recall, residual characters, mean score of TP / FP / best rejected candidate |
| `residual_phi.csv` | every gold entity not fully redacted, with its outcome and best rejected evidence |
| `false_negatives.csv` | the same, excluding partial spans |
| `false_positives.csv` | redactions overlapping no gold span and no ignore region |
| `thresholds.csv` | the threshold in force per model × entity and which config key supplied it |
| `attribution.csv` | outcome counts |

The report gains a **"Gold PHI not fully redacted (why)"** table for each document.

## 6. Why was this PHI missed?

Every gold entity gets exactly one outcome:

| outcome | meaning | what to look at |
|---|---|---|
| `detected_by_rule` / `_registry` / `_ml` / `_multiple_sources` | fully redacted | which layers protected it |
| `partial_span` | redacted, but some characters remain visible | span boundaries in `detections.csv` (e.g. a unit number after an address) |
| `below_threshold` | a live recognizer proposed it, with a score under its threshold | `best_rejected_*`: recognizer, raw score, threshold. A threshold question for **dev** calibration. |
| `policy_rejected` | evidence existed, but the entity is disabled (config, request or CLI) | your `--entities` / `--disable-entities` |
| `disabled_only` | only a recognizer or normalizer switched off in this configuration saw it | `evidence.csv`, `rejection_reason = recognizer_disabled` |
| `no_candidate` | nothing proposed it | missing rule coverage or model blind spot |
| `reconciliation_failure` | accepted evidence overlapped it but no final detection did (should never happen) | report it as a bug |
| `ASR_destroyed` | the entity is not in the transcript at all (from `asr_lost` in gold) | an ASR problem, not a detector problem |

For any span you can follow the full chain in `evidence.csv` or `responses.jsonl`. It shows every
candidate, from which recognizer, on which text view (`original` or `normalized`, with the
normalizer ids), its raw score and threshold, and why it was accepted or rejected.

## 7. Comparing recognizers and configurations

Every switch is configuration. Re-run the same inputs with different flags and compare reports.

```bash
--model none                         # rules + registry only (what does OBI add?)
--disable conv_spoken_id,conv_ordinal_date   # without specific conversational rules
--disable spoken_digits              # without a normalizer
--normalization-profile none         # no derived text views at all
--entities PERSON,DOB,PHONE,MRN      # only these entity types (aliases allowed)
--disable-entities DATE,LOCATION
--preset RULES_REGISTRY              # any preset in configs/phi_harness.yaml
--enable stanford_v2                 # EXPERIMENTAL adapter (not a production dependency)
--no-calibrated / --thresholds FILE  # thresholds: config only / a specific overlay
--set thresholds.model_entity.obi_roberta.NAME=0.6
```

- By default the dev-calibrated overlay `configs/thresholds_calibrated.yaml` is applied when it
  exists. The report header shows which overlay was in force.
- **Do not tune thresholds or rules on documents you are using to judge generalisation.** Use
  `python -m harness.phi.calibrate --dataset cadence_dev`, which refuses test datasets, and put new
  rules through dev first (`docs/rule_changelog.md`).

## 8. ASR failure vs detector failure

When evaluating ASR transcripts, list PHI that was spoken but is missing from the transcript
(deleted or garbled beyond recognition) under `asr_lost` in the gold line. Do not put it in
`entities`, because it has no span. Those items are reported as `ASR_destroyed` and **do not
count as detector misses or residual PHI**: nothing in the text can leak them.

To isolate the ASR effect, run the clean reference transcript and the ASR transcript of the same
call as two documents with their own gold. Compare the detector's recall on the clean text with
its recall on the ASR text for the entities that survived. The benchmark tooling does this
automatically for `cadence_*_asr` (`python -m harness.compare`).

## 9. Keeping PHI out of logs

- **Logs** (stderr) never contain span text, input text, registry contents or token maps. They
  carry only request ids, text hashes, counts and timings. Presidio's `log_decision_process` is
  never enabled.
- **Output files** (`report.*`, `detections.csv`, `evidence.csv`, `responses.jsonl`,
  `redacted/`) **do contain document text by design**, because you need it to judge detections.
  Keep `--output` on local, access-controlled storage. Use `--mask-text` to replace span text with
  length placeholders in every output file.
- Nothing is sent off the machine: models run locally (`OFFLINE_MODE=true` blocks downloads),
  with no LLMs or cloud services.

## 10. What this tool is not

- **It is not a production readiness claim.** Passing tests and good manual results on a few
  documents do not validate the engine. Production confidence requires governed, real Cadence data
  with independent annotation.
- **It is not a tuning loop.** Observations from manual evaluation should become dev-set examples
  and pass through the dev → frozen-test protocol before any rule or threshold changes.
