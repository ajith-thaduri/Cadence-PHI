# Configuration guide

Everything the engine does is set in `configs/phi/`. Code changes are needed only for a new rule family, validator
or normaliser *function*; entities, patterns, thresholds, operators, scopes, models and monitoring are config.

```bash
python -m harness.phi.config validate [--env prod]     # strict schema + reference integrity; non-zero exit on error
python -m harness.phi.config show --env prod           # the resolved config, with the source file of every value
```

## Composition

`configs/phi/phi.yaml` is the composition root and lists its includes in order.

- **Duplicate keys are an error.** The same leaf defined in two include files fails validation; nothing is
  silently overridden.
- **Fixed resolution order.**
  1. Includes.
  2. The environment overlay `environments/<env>.yaml`, chosen by `--env`, then `PHI_ENV`, then `base`.
  3. Generated overlays, only when `apply_generated: true`.
  4. Command-line overrides, for evaluation only.
- **Environment overlays are restricted.** They may change only the keys in `ENV_ALLOWED`
  (`harness/phi/config/loader.py`): deadlines, input size, outputs, vault, model device, telemetry, model cache
  and monitoring cadence. An environment can never change thresholds, which entities are enabled, recognizers or
  failure actions. Prod and test therefore detect the same way.
- **Generated overlays are dev-only.** `generated/thresholds_calibrated.yaml` is accepted only if its
  `_provenance.developed_on` lists **development** datasets, checked against the manifest roles. The `test`
  environment disables it, so unit-test expectations don't move when calibration is re-run.

| file | owner | what it controls |
|---|---|---|
| `entities.yaml` | privacy | catalog: description, category, Safe Harbor mapping, risk tier, enabled, default action, subtypes, core characters, outbound-guard behaviour, gate group |
| `recognizers/rules.yaml` | engineering | rule families (code in `harness/shield/rules.py`) and the entities each may emit |
| `recognizers/patterns.yaml` | engineering, privacy review | declarative regex recognizers with named validators and context words (no code); `context.side` and `context.max_words_between` bind a cue to its value |
| `recognizers/labelled.yaml` | engineering, privacy review | field label → typed identifier ("DEA registration …" → LICENSE_CERT_ID); label regexes per entity, bridge, value grammar |
| `recognizers/contextual.yaml` | engineering | conversational rules and their parameters (ID labels, DOB cues, language names) |
| `recognizers/registry.yaml` | engineering | scoped registry kinds → entities, and matching parameters |
| `recognizers/external.yaml`, `exclusions.yaml` | engineering | Presidio built-ins; allow-list of known non-PHI terms |
| `models.yaml` | ML | contextual models: enabled, `mode` (production / shadow), candidate floor, **pinned `weights_sha256`** |
| `normalization.yaml` | engineering | normalisers and normalisation profiles (`default`, `asr`) |
| `reconciliation.yaml` | engineering | score-type precedence; `type_refinements` (generic → specific; label only); `reconciliation` v2 (split bridged groups, trim field labels, merge fragments); `contextual_type_rules` (relation, employer and provider cues) |
| `policy/thresholds.yaml` | privacy | thresholds (precedence below) |
| `policy/profiles.yaml` | privacy | policy profiles: operator, whether REVIEW decisions are redacted |
| `policy/redaction.yaml` | privacy | operators, token format, **consistency scope**, vault backend / TTL / key / audit |
| `policy/failure.yaml` | security | action per failure class, deadlines, input size limit, startup self-test cases |
| `policy/outbound_guard.yaml` | privacy | independent egress re-scan: on/off, recognizer subset, block or flag |
| `policy/monitoring.yaml` | platform | canaries; detection-rate control chart (baseline path, sigma, window) |
| `runtime.yaml` | platform | Presidio settings, output text policy, telemetry, model-hit cache bound |
| `environments/*.yaml` | platform | base / dev / test / prod overlays (allow-listed keys only) |

## Thresholds

`threshold_for(entity, recognizer, model, score_type, modality)` resolves in this order:

- **`model_confidence`:** `modality.model_entity` > `model_entity` > `modality.entity` > `entity` > `model` >
  `recognizer` > `default`.
- **Deterministic and registry evidence:** `recognizer` > `deterministic_default`.

Every decision records the threshold it used and the key that supplied it (`threshold_source`).

## Common changes

| change | how | evidence required |
|---|---|---|
| Add a deterministic identifier | 1. Catalog entry in `entities.yaml` (`enabled: false`).<br>2. Pattern in `patterns.yaml` (`enabled: false`), with validators and context.<br>3. CheckList MFT/INV/DIR templates. | CheckList 100% and 0 new dev false positives, then enable both and log it in `rule_changelog.md`. |
| Change a threshold | Edit `policy/thresholds.yaml`, or re-run `python -m harness.calibrate` on **dev**. | Dev evaluation and suite regression; never test data. |
| Trial a new model safely | Add it to `models.yaml` with `enabled: true, mode: shadow`. | Shadow evidence is recorded and counted (`shadow_only_spans`) but never changes output. |
| Keep tokens consistent across a call | `redaction.consistency_scope: conversation` and send `metadata.conversation_id`. | `tests/test_vault.py`. |
| Persist the vault | `redaction.vault.backend: encrypted_file`, `key_ref: env:PHI_VAULT_KEY` (32-byte key; hex or base64). | Without the key the engine refuses to start. |
| Change what the egress guard blocks | `outbound_guard` per entity in `entities.yaml`; `action` in `policy/outbound_guard.yaml`. | `tests/test_outbound_guard.py`. |
| Add a canary | `policy/monitoring.yaml` → `canaries.cases`, using conspicuously fake values only. | `python -m harness_eval.monitoring canaries`. |

## Environments

| env | purpose | notable settings |
|---|---|---|
| `base` | default | memory vault, calibrated thresholds, `show_text: auto` |
| `dev` | development and evaluation | as base |
| `test` | unit and contract tests | placeholder thresholds (`apply_generated: false`) |
| `prod` | serving | `show_text: never`, `encrypted_file` vault, startup self-test required |
