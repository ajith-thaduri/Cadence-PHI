# Presidio in the Cadence PHI engine

> Scope this phase: English. The Presidio `RecognizerRegistry` is built only for the recognizer
> languages in `languages.map`, which is `en` by default. `AnalyzerEngine` is constructed with
> `log_decision_process=False`.

**Version in use:** `presidio-analyzer` 2.2.364 and `presidio-anonymizer` 2.2.364. Both are pinned
in the project venv and were the latest release when checked on 2026-10-05.

**Where the project lives:** it moved from `microsoft/presidio` to
`github.com/data-privacy-stack/presidio` (community-governed, still MIT). The docs were read from
raw GitHub (`docs/...`, main branch) and every API below was checked against the installed source.
`main` is slightly ahead of 2.2.364: the docs describe a YAML `text_chunker` key and a token-based
chunker, neither of which exists in 2.2.364. Code here targets 2.2.364.

> Official caveat (docs/index.md, docs/faq.md): *"there is no guarantee that Presidio will find all
> sensitive information. Consequently, additional systems and protections should be employed."*
> And (decision_process.md): *"Decision-process traces explain why PIIs were detected, but not why
> they were not detected."*
>
> Presidio is therefore the orchestration layer. It is not the safety argument. The safety
> argument is defence in depth: rules, the scoped registry, one contextual model, normalisers,
> recall-first reconciliation and residual-PHI evaluation.

## What Presidio does here (and what it does not)

| responsibility | owner | where |
|---|---|---|
| recognizer interface (`EntityRecognizer.analyze`) | Presidio | every recognizer subclasses `harness.phi.recognizers.base.PHIRecognizer(EntityRecognizer)` |
| recognizer registry, per-language selection | Presidio `RecognizerRegistry.get_recognizers(language, all_fields=True, ad_hoc_recognizers=...)` | `harness/phi/engine.py: EvidenceAnalyzer` |
| per-request recognizers (the subject's registry) | Presidio ad-hoc recognizers. This is the documented route for patient-specific known values ("Anonymizing known values" notebook). | `RegistryRecognizer`, built per request from the `RegistryStore` |
| NLP pipeline | Presidio `NoOpNlpEngine` (every recognizer is self-contained) | engine |
| context enhancement | Presidio `LemmaContextAwareEnhancer`: **off**, see below | `presidio.context_enhancement` |
| result + explanation objects | Presidio `RecognizerResult` / `AnalysisExplanation`. Our evidence travels in `recognition_metadata`. | `recognizers/base.py` |
| applying redaction operators | Presidio `AnonymizerEngine.anonymize` (`replace`, `encrypt`) | `harness/phi/redaction` |
| entity enablement, thresholds, precedence, reconciliation, REDACT/KEEP/REVIEW, confidence class, tokenisation policy, audit | **Cadence policy, not Presidio** | `harness/phi/policy`, `reconciliation`, `config.py`, YAML |

Keeping Cadence policy outside Presidio classes means Presidio stays replaceable. Only
`recognizers/base.py` and `engine.py: EvidenceAnalyzer` import `presidio_analyzer`.

## Integration decisions and why

1. **`EvidenceAnalyzer.analyze_all` instead of `AnalyzerEngine.analyze`.** Verified in the 2.2.364
   source, `analyze()` does the following in order:
   - drops results below a score threshold (per call, or per recognizer and entity);
   - runs `EntityRecognizer.remove_duplicates`, which drops results with score 0 and any result
     contained in a same-type result;
   - applies the allow-list only after thresholding.

   All of that throws away evidence silently. A second recognizer's provenance disappears when its
   span lies inside another one's. `analyze_all` uses the same registry, NLP engine and optional
   `_enhance_using_context`, but returns every raw result. Thresholds are then applied by Cadence
   policy, with the winning config key recorded on each evidence item.

2. **Models are `EntityRecognizer`s (`ModelRecognizerAdapter`), not an NLP engine.** This is the
   pattern Presidio documents for running several NER models side by side ("Using a transformers
   model as an EntityRecognizer"). Each model is one recognizer instance per language sharing one
   set of weights. Our adapters are used instead of Presidio's `HuggingFaceNerRecognizer` /
   `GLiNERRecognizer` for four reasons:
   - The model revisions are pinned and the native→canonical label mappings were validated in the
     benchmark phase. Presidio's HF recognizer passes unmapped labels through, and its default
     mapping is not clinical.
   - We need raw per-span scores down to a 0.1 candidate floor for calibration. Presidio's HF and
     GLiNER recognizers filter at their own `threshold=0.3` internally.
   - Presidio's HF chunker is character-based (400 chars, 40 overlap) and does not know about the
     512-token limit. Our adapters chunk on tokens using the model's own tokenizer.
   - Offline operation is explicit (`OFFLINE_MODE`; no auto-downloads).

   Presidio's built-in recognizers remain a drop-in alternative behind the same contract.

3. **NoOp NLP engine.** No recognizer needs spaCy tokens. spaCy NER would add nothing (every spaCy
   entity gets a fixed 0.85 score) and would add a model download.
   `presidio.context_enhancement: false`, because lemma context boosting needs lemmas, which the
   NoOp engine does not produce. If it is enabled later:
   - the boost is +0.35 with a floor of 0.4;
   - the default substring matching is loose, so use whole-word context;
   - the change would be recorded as `presidio_context_improvement` in the evidence metadata and
     would never hide the raw score.

4. **Registry as ad-hoc recognizers, resolved from opaque references.** A request carries only a
   `RegistryContext` (`subject_context_id`, directory versions). `RegistryStore.resolve` returns
   that subject's record plus the versioned directories, and the recognizer is attached for that
   one call. Presidio's deny-list recognizers do not expand name variants. Ours handle case,
   possessives, nicknames, single-token parts and conservative fuzzy matching, and report
   `match_type`, `registry_type` and `registry_version`. Gold-derived registries are refused.

5. **Thresholds: Cadence's, not Presidio's.** Presidio's per-call `score_threshold=` overrides all
   per-recognizer thresholds, so it is never passed. Presidio's `score_thresholds` mechanism is
   applied only inside `AnalyzerEngine.__remove_low_scores`, which is bypassed. The resolution order
   is in `configs/phi_harness.yaml` and `docs/engine_contract.md`.

6. **Anonymizer.**
   - Reconciliation guarantees non-overlapping spans before redaction. Presidio's anonymizer would
     otherwise drop any result fully contained in another, whatever its type.
   - Each detection gets its own operator config, so the output↔detection mapping is 1:1 and is
     verified: the engine refuses output if the span count changes.
   - `encrypt` is Presidio's AES-CBC with a random IV, so it is reversible but not deterministic.
   - `hash` uses a random salt per entity unless one is supplied, so it is not used for
     re-identification.
   - Cadence's default operator, deterministic reversible **TOKENIZE** (`[NAME_1]`), is a
     `replace` with values computed by our TokenVault. That is the pattern of the official
     pseudonymization sample.
   - The vault (token → surface) never enters a response or a log.

7. **Never used (they send text off the machine):**
   - `LMRecognizer` / LangExtract recognizers (LLM calls; Azure by default);
   - the AHDS recognizer and the `surrogate_ahds` operator (Azure);
   - `log_decision_process=True`, which writes raw entity text to the `decision_process` logger.
     Only counts, ids and hashes are logged (`options.strict_logging`).

8. **Fail closed on config.** `AnalyzerEngineProvider` silently falls back to its default config if
   the YAML is missing or will not parse. We do not use it. `HarnessConfig.load` validates every
   section and raises `ConfigError`.

## Presidio components available but off by default

These are comparison and defence-in-depth options, all mapped to the Cadence taxonomy (one
`enabled:` flag each):

| config id | Presidio recognizer |
|---|---|
| `presidio_email` | `EmailRecognizer` |
| `presidio_phone` | `PhoneRecognizer` (phonenumbers) |
| `presidio_url` | `UrlRecognizer` |
| `presidio_date` | `DateRecognizer` |
| `presidio_us_mbi` | `UsMbiRecognizer` (Medicare Beneficiary Identifier → MEMBER_ID) |
| `presidio_us_npi` | `UsNpiRecognizer` |
| `presidio_us_ssn` | `UsSsnRecognizer` |

They read the original text only. Spoken forms reach them only through the normalised view, if
`normalized_views_feed` includes `rule`.

## Sources read

From `data-privacy-stack/presidio`, main branch:
- `docs/index.md`, `docs/faq.md`, `docs/project_transition.md`, `docs/learn_presidio/concepts.md`
- `docs/analyzer/index.md`, `adding_recognizers.md`, `analyzer_engine_provider.md`,
  `recognizer_registry_provider.md`, `decision_process.md`, `customizing_nlp_models.md`,
  `nlp_engines/transformers.md`
- `docs/anonymizer/index.md`, `docs/evaluation/index.md` (recommends F2)
- `docs/samples/python/gliner.md`, `no_code_config.ipynb`, "Anonymizing known values.ipynb",
  `pseudonymization.ipynb`, `integrating_with_external_services.ipynb`

Installed source: `presidio_analyzer/{analyzer_engine,entity_recognizer,recognizer_result,analysis_explanation,score_thresholds}.py`,
`recognizer_registry/`, `nlp_engine/`, `predefined_recognizers/ner/`, `context_aware_enhancers/`,
`chunkers/`, and `presidio_anonymizer/{anonymizer_engine.py,operators/}`.
