# Clinical PHI Model Benchmark (v2)

A local, reproducible harness for deciding which pretrained clinical PHI/PII detector can
serve as the **contextual ML layer of the Cadence PHI Shield**, and how much PHI remains
**before AWS Bedrock** when that model is combined with deterministic rules and registry
matching.

This is an evaluation harness, not the production PHI Shield. It uses no real Cadence/HHNS
PHI, trains no models, and calls no external inference APIs. The network is used only to
download public model weights and open-licence corpora.

## Evidence labels (used on every table)

| Label | Meaning |
|---|---|
| `[PUBLISHED REAL DATA]` | Results others published on real clinical data (author-reported or independent) — `configs/published_evidence.yaml` |
| `[OUR REAL-DATA REPRODUCTION]` | Our run on a real clinical PHI corpus — **none exists yet** (i2b2/PhysioNet/SHIELD need DUA/credentialed access) |
| `[OUR PUBLIC-CORPUS TEST — MEDDOCAN]` | Our run on MEDDOCAN: real Spanish clinical case text, **synthetic** PHI |
| `[OUR SYNTHETIC BENCHMARK]` | Our controlled stress test (fabricated data) |
| `[ENGINEERING INFERENCE]` / `[UNKNOWN]` | Reasoning, or questions the evidence cannot answer |

Synthetic and real results are never combined into one score.

## Models

| Key | Checkpoint | Role |
|---|---|---|
| stanford_base / stanford_v2 / stanford_i2b2 | StanfordAIMI deidentifier base / v2 / with-radiology-reports-and-i2b2 | core / candidate |
| obi_roberta | obi/deid_roberta_i2b2 | candidate (independent real-data evidence) |
| mednotes_gliner | Lekhansh/modern-gliner-large-mednotes-deidentifier (the LOGICAL paper's model) | core |
| kg_pii_large / kg_pii_base | knowledgator/gliner-pii-{large,base}-v1.0 | candidate |
| kg_stream_pii | knowledgator/gliner-stream-pii-v1.0 (streaming sessions) | candidate |
| labbiomedica_es | LabBiomedica Spanish clinical de-id | Spanish specialist |
| logical_gliner | BioMike/logical-modern-gliner-bi-large-v1.0 | **excluded — provenance invalid (logic-QA model)** |
| kindlab_roberta | KindLab/roberta-deid | **excluded — degenerate checkpoint (predicts O everywhere)** |

Exact revisions, licences, training data and limitations: `configs/models.yaml`,
`docs/model_inspection.md`. Reviewed-but-not-added candidates are listed there too.

## Setup, download, run

```bash
uv venv -p 3.12 .venv && uv pip install -p .venv/bin/python -r requirements.txt
source .venv/bin/activate
OFFLINE_MODE=false python -m harness.download          # pinned model revisions (network)
python -m harness.download_public                       # MEDDOCAN, CC BY 4.0, checksum-verified (network)

python -m harness.run_all                               # everything, offline (~1-2 h on Apple M3)
pytest -q                                               # unit tests (no weights needed)
```

Individual steps:

```bash
python -m harness.list_models
python -m harness.inspect_model --model obi_roberta
python -m harness.generate_synthetic
python -m harness.evaluate --all-models                 # synthetic + MEDDOCAN, all shield stages
python -m harness.evaluate --model obi_roberta --datasets synthetic
python -m harness.benchmark --all-models --device cpu
python -m harness.report                                # reports/final_report.{html,md} + CSVs
# real data, if you hold a licence:
python -m harness.evaluate --all-models --dataset i2b2_2014 --data-path /secure/i2b2 --allow-local-public-dataset
```

## What is evaluated

**Data.** Synthetic v2 (dev 87 / test 180 documents) uses *disjoint template families*
for dev and test. It covers 15 subsets: telephone/ASR, dictation, care plans, summaries,
timelines, retrieved excerpts, contextual PHI, hard identifiers (natural formats first),
date forms, Spanish, code-switching, long documents (500–16,000 tokens) and negative
controls for over-redaction. MEDDOCAN dev/test keep the official split. Restricted real
corpora are recorded as `NOT RUN — DATASET UNAVAILABLE`.

**Pipeline stages** (`harness/shield/`): `MODEL_ONLY`, `DETERMINISTIC_ONLY`,
`DETERMINISTIC_REGISTRY`, `MODEL_DETERMINISTIC`, and `MODEL_DETERMINISTIC_REGISTRY` (the
full shield). Components are deterministic detectors (phone, email, URL, member/insurance
ID, MRN, NPI, account, ZIP, dates/DOB/relative dates, addresses, titled names, EN/ES
spoken-digit normalisation), registry matching (member, providers, staff, facilities,
employers, contacts and known IDs, with fuzzy name tokens), reconciliation (union with
word-boundary expansion) and consistent-token redaction with exact restoration.

**Metrics.**
- Entity full-span recall, character recall, residual PHI character rate
- Complete-document sanitization and precision/over-redaction (including negative-control documents)
- Per-category recall, plus gates for NAME, DOB, PHONE, ADDRESS, MEMBER_ID, MRN, NPI, PROVIDER and FACILITY
- ASR, Spanish, code-switching, long-document and hard-identifier recall
- **RESIDUAL_PHI_AFTER_SHIELD**: per-document gold PHI characters left in the *redacted output*, reported as mean, median, P95, worst document and false-all-clear rate

**Calibration.** Thresholds are chosen on dev only, by max F2 (recall-weighted),
separately for the model alone and for the full shield, then applied unchanged to test.

**Long documents.** Each long document runs under four strategies: `author` (each model's
own pipeline), `production` (overlapping context windows), `naive` (hard cuts) and
`truncate` (no chunking). A chunk-boundary test places names, addresses, phones, MRNs and
dates straddling the window boundary.

**False negatives.** Every FN is exported with its failure categories: model missed,
wrong boundary, wrong type, context, ASR, long document, identifier format, Spanish,
registry and chunk boundary.

## Output

`results/<timestamp>/` holds `config.json`, `models/`, `metrics/` (per model, all pairs and
stages), `predictions/`, `false_negatives/`, `false_positives/`, `raw/` (per-document
safety), `latency/` (MPS and CPU), `plots/` and `reports/`. `python -m harness.report`
copies the latest report and CSVs to `reports/`: `per_model_metrics`,
`pipeline_stage_metrics`, `per_category_metrics`, `subset_metrics`, `false_negatives`,
`latency_metrics`, `model_provenance`, `dataset_provenance` and `published_evidence`.

## Rules of the benchmark

- No winner score. The recommendation is a documented procedure (real-clinical evidence
  tier, then residual PHI after the full shield, then contextual recall) and it keeps
  model selection separate from architecture.
- No manual judgement inside the loop. Human review of false negatives happens after evaluation.
- Gates are internal engineering gates. No model or pipeline is called "HIPAA compliant";
  that is an architectural and legal determination.
- Privacy: `OFFLINE_MODE=true` by default, no document text in logs, and non-synthetic text
  redacted from artifacts unless `--debug --show-public-text`.

## Known limitations

- No real-data reproduction was possible.
- MEDDOCAN PHI is synthetic.
- The deterministic rules and the synthetic templates share an author, which biases synthetic rule recall upward.
- ASR is simulated text noise, not real speech-recognition output.
- Several categories have small support.

See section 20 of the report.

## PHI detection engine v2 (Presidio-orchestrated)

`harness/phi/` holds the Cadence PHI detection engine. The application entry point is
`PHIEngine.analyze(DetectionRequest)` / `.redact(...)`. The contract is documented in
`docs/engine_contract.md`, the Presidio integration in `docs/presidio_architecture.md`, and the
generated entity responsibility matrix in `docs/entity_responsibility_matrix.md`. Rule provenance
is in `docs/rule_changelog.md`; results and review answers are in `results/phi/ENGINE_V2_RESULTS.md`.

```bash
OFFLINE_MODE=true python examples/analyze_example.py                      # synthetic end-to-end example
python -m harness.phi.config validate --env prod                         # strict layered config (configs/phi/)
python -m harness.calibrate --dataset cadence_dev                        # dev only; writes configs/phi/generated/
python -m harness.evaluate --config configs/phi/phi.yaml --env dev --dataset cadence_dev_text,cadence_dev_asr
python -m harness.compare  --config configs/phi/phi.yaml --dataset cadence_dev
python -m harness_eval.export --dataset cadence_test                     # canonical JSONL requests + gold
python -m harness.phi.matrix                                             # regenerate the responsibility matrix
```

### Operator console (web UI) - docs/ui_guide.md

A local web console for non-technical and technical users: redact text and files, run checks and evaluations,
read results and release readiness, monitor, and browse configuration and documentation.

```bash
uv pip install --python .venv/bin/python -e '.[ui]'   # FastAPI / uvicorn
scripts/build_ui.sh                                   # builds frontend/ (React + TypeScript) into harness_ui/static
python -m harness_ui serve                            # http://127.0.0.1:8765 (local single user; see docs/ui_guide.md for login + roles)
```

### Production harness (plan P0-P5) - docs/config_guide.md, evaluation_methodology.md, release_gates.md, security_controls.md

```bash
pytest                                                    # fast tier (no model weights), ~15 s
pytest -m model                                           # model tier: local OBI weights, forced offline
python -m harness_eval.checklist run --tier all           # T1 behavioural (G5)
python -m harness_eval.suite regress --model none --baseline baseline_fast.json   # T2 regression, fast tier (G4)
python -m harness_eval.suite regress                      # T2 regression, model tier
python -m harness_eval.perf all --duration 3600           # T7 latency / throughput / soak / cold start / determinism (G8)
python -m harness_eval.monitoring baseline                # T8 detection-rate baseline (dev data only)
python -m harness_eval.monitoring canaries                # run the configured canaries once
python -m harness_eval.gates run --with-model             # G1-G9 go / no-go (minimum across gates)
python -m harness_eval.cards all                          # regenerate docs/system_card.md + docs/datasheets/
```

### Manual evaluation (English; same engine as the application) - see docs/manual_evaluation.md

```bash
OFFLINE_MODE=true python -m harness.phi.analyze --input sample.txt                                  # one document, console
OFFLINE_MODE=true python -m harness.phi.analyze --input sample.txt --redact --output results/example
OFFLINE_MODE=true python -m harness.phi.analyze --input-dir data/manual_eval --output results/manual_eval
OFFLINE_MODE=true python -m harness.phi.analyze --input requests.jsonl --output results/batch          # JSONL batch
OFFLINE_MODE=true python -m harness.phi.analyze --input-dir data/manual_eval_example --registry data/manual_eval_example/registry \
       --gold data/manual_eval_example/gold.jsonl --redact --output results/manual_eval/example        # with optional gold
OFFLINE_MODE=true python -m harness.phi.calibrate --dataset cadence_dev                               # dev only; refuses test
```
