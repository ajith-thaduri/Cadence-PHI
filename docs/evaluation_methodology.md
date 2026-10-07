# Evaluation methodology (TEVV)

The evaluation layer is `harness_eval/`. The runtime never imports it (`tests/test_boundaries.py`). Evaluation calls
the same public engine API that production uses (`PHIEngine.redact` / `collect`) and scores the output outside
the engine.

## Test tiers

| tier | data | command | gate |
|---|---|---|---|
| T1 behavioural (CheckList) | template-generated, conspicuous fakes (`eval_suites/checklist_v1`) | `python -m harness_eval.checklist run --tier fast\|model\|all` | G5 |
| T2 prompt suite | 215 curated fabricated prompts, 52 holdout (`eval_suites/english_v1`) | `python -m harness_eval.suite run\|regress [--model none --baseline baseline_fast.json]` | G4 |
| T3 realistic fabricated | `cadence_dev_*` (dev), `cadence_text` / `cadence_asr` (test) | `python -m harness.evaluate --config configs/phi/phi.yaml --dataset ...` | development evidence |
| T4 release | **real, governed, double-annotated data**; none exists yet | same scorer on a `role: release`, `phi_class: real_governed` dataset | G1–G3, G6 |
| T5 fault injection | synthetic | `tests/test_failsafe.py` | G7 |
| T6 security / privacy | synthetic sentinels | `tests/test_telemetry_privacy.py`, `test_vault.py`, `test_outbound_guard.py`, `test_boundaries.py` | G7 |
| T7 performance | synthetic, bucketed by length | `python -m harness_eval.perf all [--duration 3600]` | G8 |
| T8 monitoring | live, unlabelled | canaries + detection-rate control chart (`harness/phi/monitoring.py`) | G9 |

## Data roles and integrity

- **The manifest is the authority.** `configs/eval/datasets.yaml` gives each dataset a **role** (dev / test / suite /
  release), a `phi_class` and a SHA-256.
- **Hashes are verified on every load.** A changed file is an error until the manifest is re-signed and the change
  is logged.
- **Purpose is enforced by role.** Calibration, rule development and the monitoring baseline refuse non-dev
  datasets (`manifests.require_role`).
- **Contamination is checked.** `python -m harness_eval.contamination` flags dev/test near-duplicates (5-gram
  Jaccard); the last check found 0 pairs at 0.3.
- **Every run goes in the ledger.** `results/ledger.jsonl` is append-only. Each entry records the code commit and
  fingerprint, config hash, catalog version, threshold overlays, model revisions, datasets and roles, and
  whether test data was accessed.

## Rules against benchmark gaming

These rules are enforced by process, and by code where possible:

- No threshold tuning, rule development, registry construction or annotation changes from test or suite results.
- No template- or document-specific rules, and no manual fixing of predictions.
- No silently excluding difficult examples. Out-of-scope documents are excluded explicitly and reported.
- Every rule change goes in `docs/rule_changelog.md` with its *source of evidence*: dev data, a published format
  specification, or the T1 behavioural spec.
- Holdout prompts are scored, but their details are hidden from reports unless `--show-holdout` is passed.

## Scoring (`harness_eval/scoring.py`)

- **Leak view (primary).** Residual PHI is the share of gold *core characters* still readable after redaction. Core
  characters exclude titles, keyword affixes and edge function words, using the catalog `core_chars` rules.
  Token leak rate is also reported, and cross-checked by an independent implementation (`xcheck.py`).
- **Entity view.** Recall is reported as full-span, any-overlap, strict (exact type) and type-correct, per entity,
  modality and stratum.
- **Document view.** The fully sanitised rate and the direct-identifier sanitised rate.
- **Independent redaction check** (`redaction_check.py`). It recomputes coverage from the redacted *output*: verbatim
  and normalised leaks, output-slice errors, must-not-redact violations, derived tokens, and vault round-trip.
- **Statistics** (`stats.py`).
  - Wilson intervals.
  - Rule of three for zero-miss strata.
  - Cluster bootstrap over documents (1000 replicates).
  - Paired approximate randomisation for A/B comparisons.
  - Fewer than 300 mentions (direct identifiers) is reported as `insufficient_evidence`.
- **Attribution** (`attribution.py`). A deterministic stage decision tree assigns each miss to:
  - `ASR_destroyed`
  - `no_candidate`
  - `disabled_only`
  - `normalization_failure`
  - `below_threshold`
  - `policy_rejected`
  - `reconciliation_failure`
  - `partial_span`
  - `redaction_failure`

  It also records the source groups (DETERMINISTIC / CONVERSATIONAL / REGISTRY / MODEL:x) and sole-catch credit.

## Strict typed evaluation

`python -m harness_eval.strict_eval --input <jsonl> [--whole-document]` scores each gold entity as correct (fully
hidden **and** an accepted type), wrong type, partial or missing, and counts false positives. Honorifics and
possessives are neutral. The CheckList also reports **MFT type accuracy**. `data/diagnostic/user_cases_v1.jsonl`
(role dev) is the first strict-review set.

## Current evidence (2026-10-06, fabricated data only)

| measurement | value |
|---|---|
| `cadence_dev_text` residual PHI (mean) | 5.8% (95% CI 4.3–7.6%); high-risk full recall 92.2% |
| `cadence_dev_asr` residual PHI (mean) | 20.0%; high-risk full recall 78.9%. 61 entities lost by ASR before detection. |
| Redaction integrity (dev) | 0 output-slice errors, 0 round-trip failures, 0 must-not-redact violations |
| CheckList | fast 1319/1319; model 62/62 (name-origin invariance included) |
| Prompt suite regression | 0 regressions on both tiers |
| Release decision | **NO-GO**. G1–G3/G6 have insufficient evidence (no T4 data); G8 fails on CPU. See `release_gates.md`. |
