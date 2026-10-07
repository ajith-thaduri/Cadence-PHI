# Presidio 2.2.364 API notes (for a local clinical PHI engine)

Verified against installed source:
`/Users/kvssarma/Desktop/Models/clinical_phi_model_benchmark/.venv/lib/python3.12/site-packages/presidio_analyzer` and `.../presidio_anonymizer` (2.2.364).
Installed alongside: gliner 0.2.29, transformers 5.16.1, torch 2.14.1, spacy 3.8.16.

## 0. Where the project lives now (checked 2026-10-05)
- Repo moved: **https://github.com/data-privacy-stack/presidio** (GitHub API: `microsoft/presidio` returns "Moved Permanently"). Community-governed, still MIT. See `docs/project_transition.md`.
- Latest GitHub release tag = **2.2.364** (2026-07-22), so the installed version is current. `main` is ahead of the release in some areas (see "main vs installed" below).
- `https://microsoft.github.io/presidio/` returned HTTP 200 when checked (it may redirect). I read raw docs from `https://raw.githubusercontent.com/data-privacy-stack/presidio/main/docs/...`.
- Docker images now go to `ghcr.io/data-privacy-stack/presidio-*`. MCR images are frozen.

Doc pages actually read (raw GitHub, main branch):
- docs/index.md, docs/faq.md, docs/project_transition.md, docs/learn_presidio/concepts.md, docs/evaluation/index.md
- docs/analyzer/index.md, adding_recognizers.md, analyzer_engine_provider.md, recognizer_registry_provider.md, decision_process.md, customizing_nlp_models.md, nlp_engines/transformers.md
- docs/anonymizer/index.md
- docs/samples/python/gliner.md, no_code_config.ipynb, "Anonymizing known values.ipynb", pseudonymization.ipynb, integrating_with_external_services.ipynb (directory listing also shows transformers_recognizer/, example_remote_recognizer.py, span_marker_recognizer.py, flair_recognizer.py, example_custom_lambda_anonymizer.py, encrypt_decrypt.ipynb, ner_model_configuration.ipynb)

**Official caveat** (docs/index.md and docs/faq.md, word for word): "Presidio can help identify sensitive/PII data in un/structured text. However, because it is using automated detection mechanisms, there is no guarantee that Presidio will find all sensitive information. Consequently, additional systems and protections should be employed."
Also: "Decision-process traces explain why PIIs were detected, but not why they were not detected!" (decision_process.md). And evaluation/index.md says: "In PII detection, recall is often more important than precision ... we recommend to use the β=2 score".

---

## 1. AnalyzerEngine (analyzer_engine.py)

```python
AnalyzerEngine(registry: RecognizerRegistry = None, nlp_engine: NlpEngine = None,
               app_tracer: AppTracer = None, log_decision_process: bool = False,
               default_score_threshold: float = 0, supported_languages: List[str] = None,
               context_aware_enhancer: Optional[ContextAwareEnhancer] = None)
```
If you pass no nlp_engine, it builds `NlpEngineProvider().create_engine()`, which loads the spaCy `en_core_web_lg` model. If you pass no registry, it loads the predefined recognizers. If you pass no enhancer, it uses `LemmaContextAwareEnhancer()`.

```python
analyze(self, text: str, language: str, entities: Optional[List[str]] = None,
        correlation_id: Optional[str] = None, score_threshold: Optional[float] = None,
        return_decision_process: Optional[bool] = False,
        ad_hoc_recognizers: Optional[List[EntityRecognizer]] = None,
        context: Optional[List[str]] = None, allow_list: Optional[List[str]] = None,
        allow_list_match: Optional[str] = "exact",
        regex_flags: Optional[int] = re.DOTALL | re.MULTILINE | re.IGNORECASE,
        nlp_artifacts: Optional[NlpArtifacts] = None) -> List[RecognizerResult]
```
Pipeline order (from the source):
1. `registry.get_recognizers(language, entities, all_fields, ad_hoc_recognizers)`
2. `nlp_engine.process_text` runs, unless you passed `nlp_artifacts` yourself.
3. Each recognizer's `.analyze()` runs. The engine then adds `recognizer_name` and `recognizer_identifier` to `recognition_metadata`.
4. `_enhance_using_context`: the recognizer-level `enhance_using_context` runs first, then the engine-level `context_aware_enhancer`.
5. If `log_decision_process` is on, all results are traced at this point.
6. `__remove_low_scores` drops low scores. **Thresholding happens BEFORE dedup.**
7. `EntityRecognizer.remove_duplicates`
8. allow_list filtering:
   - `"exact"`: the span text must equal an entry. This is case-sensitive.
   - `"regex"`: uses `re.search` over the span text, with a timeout (REGEX_TIMEOUT_SECONDS env, default 60). On timeout the result is kept.
9. If `return_decision_process` is False, `analysis_explanation` is set to None.

**score_threshold precedence** (source plus analyzer_engine_provider.md). The per-request `score_threshold=` overrides everything else **for that request**. Otherwise, each result gets the threshold of the recognizer that produced it, picked in this order:
1. the entity-specific threshold `recognizer.score_thresholds[entity_type]`
2. the recognizer default `score_thresholds["default"]`
3. the engine `default_score_threshold` (default 0)

Consequence: if you pass `score_threshold` per call, all per-recognizer thresholds are silently bypassed.

**Does the pipeline drop evidence? Yes, in several places, and nothing records it:**
- Results below threshold are discarded for good. Nothing keeps them unless `log_decision_process=True`, which only writes them to a logger.
- `remove_duplicates` does four things:
  - It runs `set()` on the results, where equality means same start, end, entity_type and score.
  - It sorts by (-score, start, -length).
  - It **drops any result whose score == 0**.
  - It drops any result that is *contained in* an already-kept result **of the same entity_type**. Results of different entity types on overlapping or identical spans are all kept. The kept result's `recognition_metadata` / `analysis_explanation` is the only provenance that survives, so when two recognizers agree on a span you lose the second recognizer's identity.
- `PatternRecognizer` also calls `remove_duplicates` internally. It drops matches whose `validate_result` is False (score set to 0) or whose `invalidate_result` is True.
- The allow-list drops matches after thresholding.
- => For a leakage-auditing engine, capture raw per-recognizer outputs yourself. Options: call each recognizer's `.analyze()` directly, or wrap recognizers. Do not treat `AnalyzerEngine.analyze` output as the full evidence set.

**BatchAnalyzerEngine** (batch_analyzer_engine.py):
```python
BatchAnalyzerEngine(analyzer_engine: Optional[AnalyzerEngine] = None)
analyze_iterator(texts: Iterable[str|bool|float|int], language: str, batch_size: int = 1,
                 n_process: int = 1, **kwargs) -> List[List[RecognizerResult]]
analyze_dict(input_dict, language, keys_to_skip=None, batch_size=1, n_process=1, **kwargs)
  -> Iterator[DictAnalyzerResult]
```
It batches only the NLP engine step (`nlp_engine.process_batch`). Then it calls `analyze(..., nlp_artifacts=...)` once per text, and kwargs are forwarded. Recognizers such as HF and GLiNER still run once per text.

## 2. RecognizerRegistry / RecognizerRegistryProvider / AnalyzerEngineProvider

```python
RecognizerRegistry(recognizers: Optional[Iterable[EntityRecognizer]] = None,
                   global_regex_flags: Optional[int] = re.DOTALL|re.MULTILINE|re.IGNORECASE,
                   supported_languages: Optional[List[str]] = None)   # default ["en"]
.load_predefined_recognizers(languages=None, nlp_engine=None, countries=None)
.add_nlp_recognizer(nlp_engine)
.get_recognizers(language, entities=None, all_fields=False, ad_hoc_recognizers=None)
.add_recognizer(recognizer)                      # must be EntityRecognizer
.remove_recognizer(recognizer_name, language=None)
.add_pattern_recognizer_from_dict(recognizer_dict)   # pops "score_thresholds" and applies it
.add_recognizers_from_yaml(yml_path)
.get_supported_entities(languages=None)
.get_country_codes()
```
`RecognizerRegistryProvider(conf_file=None, registry_configuration: Optional[Dict]=None, nlp_engine=None).create_recognizer_registry()`. The configuration is validated by pydantic models in `input_validation/yaml_recognizer_models.py`.

`AnalyzerEngineProvider(analyzer_engine_conf_file=None, nlp_engine_conf_file=None, recognizer_registry_conf_file=None).create_engine()`. It reads `supported_languages` and `default_score_threshold`, plus either `nlp_configuration` and `recognizer_registry` in a single file or three separate files.
- **Caveat:** if the analyzer conf file is missing or unparsable, it **silently falls back to the default config** and only logs a warning. A PHI system should fail closed: check that the file exists and parses before you call it.
- `supported_languages` must be identical across analyzer and registry config.

What the YAML can configure:
- **Top level:** `supported_languages`, `default_score_threshold`, `nlp_configuration`, `recognizer_registry`.
- **Registry:** `supported_languages`, `global_regex_flags` (26 = I|M|S), `supported_countries`, `recognizers: [...]`.
- **Fields on every recognizer** (BaseRecognizerConfig): `name`, `class_name`, `enabled`, `type` (predefined | custom), `supported_language` / `supported_languages` (a list of codes, or `{language, context}` objects), `context`, `supported_entity` / `supported_entities`, `score_thresholds` (`{default: x, ENTITY: y}`).
- **Extra fields for `type: custom`** (these become a PatternRecognizer): `patterns` (`name`, `regex`, `score`), `deny_list`, `deny_list_score` (default 1.0), `country_code`.
- **Predefined recognizer kwargs:**
  - HuggingFaceNerRecognizer: `model_name`, `tokenizer_name`, `label_mapping`, `threshold`, `aggregation_strategy`, `chunk_size`, `chunk_overlap`, `device`, `label_prefixes`.
  - GLiNERRecognizer: `model_name`, `flat_ner`, `multi_label`, `threshold`, `map_location`, `load_onnx_model`, `onnx_model_file`, `entity_mapping`.
  - LangExtract: `config_path`.
- **NLP section:** `nlp_engine_name` (spacy | stanza | transformers | slim | no_op), `models`, `ner_model_configuration`.
- Examples to follow: `conf/default_recognizers.yaml`, `conf/example_recognizers.yaml`, `conf/slim.yaml` (slim NLP + GLiNERRecognizer instead of spaCy NER), `conf/transformers.yaml`, `conf/no_op.yaml`.

**main vs installed discrepancy:** recognizer_registry_provider.md on main documents a YAML `text_chunker: {chunker_type: tokenizer|character, ...}` key and a `tokenizer` chunker (`tokenizer_based_text_chunker.py`). **Neither is in 2.2.364.** Installed `_CHUNKER_REGISTRY` = {"character"} only, and the loader has no `text_chunker` dict handling. In 2.2.364, use `chunk_size` / `chunk_overlap` kwargs, or pass a `CharacterBasedTextChunker` in code.

## 3. Recognizer classes

```python
EntityRecognizer(supported_entities: List[str], name: str = None, supported_language: str = "en",
                 version: str = "0.0.1", context: Optional[List[str]] = None,
                 country_code: Optional[str] = None,
                 score_thresholds: Optional[Dict[str, float]] = None)
  abstract load(); abstract analyze(text, entities, nlp_artifacts) -> List[RecognizerResult]
  enhance_using_context(text, raw_recognizer_results, other_raw_recognizer_results, nlp_artifacts, context=None)
  id -> f"{name}_{id(self)}"; static remove_duplicates(results); from_dict/to_dict
LocalRecognizer(ABC, EntityRecognizer)   # marker: runs in-process
RemoteRecognizer(ABC, EntityRecognizer)  # marker for external services; __init__(supported_entities, name, supported_language, version, context=None)
Pattern(name: str, regex: str, score: float)   # validates the regex compiles and 0<=score<=1
PatternRecognizer(supported_entity: str, name=None, supported_language="en", patterns: List[Pattern]=None,
                  deny_list: List[str]=None, context: List[str]=None, deny_list_score: float=1.0,
                  global_regex_flags=re.DOTALL|re.MULTILINE|re.IGNORECASE, version="0.0.1",
                  country_code=None)
  analyze(text, entities, nlp_artifacts=None, regex_flags=None)
  validate_result(pattern_text) -> Optional[bool]   # True => score 1.0, False => 0 (dropped)
  invalidate_result(pattern_text) -> Optional[bool] # True => dropped
```
- PatternRecognizer uses the third-party **`regex`** module (`import regex as re`) with a per-pattern timeout (REGEX_TIMEOUT_SECONDS, default 60s). On timeout the pattern is skipped with only a warning, so a timeout can cause **silent recall loss**.
- The deny-list is compiled into one regex, `(?:^|(?<=\W))(alt1|alt2|...)(?:(?=\W)|$)`, built from `re.escape`d terms. It is case-insensitive under the default flags, and its score is `deny_list_score`.
  - This is the built-in mechanism for patient-specific "known values". docs/samples/python/"Anonymizing known values.ipynb" shows both a static deny-list and **per-request `ad_hoc_recognizers=[PatternRecognizer(deny_list=[name])]`** built from a record's structured fields.
  - The deny-list does not handle name variants such as initials, possessives, tokenization differences, or OCR/ASR misspellings. You must expand the list yourself (or add fuzzy matching).
- **`PatternRecognizer.__init__` does NOT accept `score_thresholds`.** Set it after construction (`rec.score_thresholds = {...}`), or through the YAML or `add_pattern_recognizer_from_dict`, which pop it and set the property.
- `score_thresholds.py`:
  - `normalize_score_thresholds` validates that the input is a Mapping, the keys are non-empty and unpadded strings, and the values are numeric (not bool) in [0, 1].
  - It is applied **only** in `AnalyzerEngine.__remove_low_scores`, keyed by `recognition_metadata["recognizer_identifier"]`.
  - A result whose recognizer id is missing, or does not match a selected recognizer, falls back to `default_score_threshold`.
- Predefined generic recognizers useful for PHI: Email, Phone (uses `phonenumbers`), URL, IP, MAC, Date, CreditCard, IBAN, Crypto. US recognizers: SSN, ITIN, Passport, DriverLicense, Bank, ABA routing, **MedicalLicense, US_NPI, US_MBI**. Spain recognizers also exist (NIF/NIE).

## 4. RecognizerResult / AnalysisExplanation

```python
RecognizerResult(entity_type: str, start: int, end: int, score: float,
                 analysis_explanation: AnalysisExplanation = None, recognition_metadata: Dict = None)
  intersects(other)->int, contained_in, contains, equal_indices, has_conflict, to_dict (= __dict__), from_json
  __eq__/__hash__ use (start, end, score, entity_type) only
```
recognition_metadata keys (class constants):
- `recognizer_name` (RECOGNIZER_NAME_KEY)
- `recognizer_identifier` (RECOGNIZER_IDENTIFIER_KEY)
- `is_score_enhanced_by_context` (IS_SCORE_ENHANCED_BY_CONTEXT_KEY). Set this flag only if a recognizer boosts its own score in `enhance_using_context`. When it is set, the engine-level enhancer skips that result.

Custom keys are allowed, because it is just a dict.

```python
AnalysisExplanation(recognizer: str, original_score: float, pattern_name: str = None, pattern: str = None,
                    validation_result: bool = None, textual_explanation: str = None, regex_flags: int = None)
  fields: recognizer, pattern_name, pattern, original_score, score, textual_explanation,
          score_context_improvement (=score-original_score), supportive_context_word, validation_result, regex_flags
  set_improved_score(score), set_supportive_context_word(word), append_textual_explanation_line(text)
```

## 5. NLP engines (nlp_engine/)
`NlpEngineProvider(nlp_engines: Optional[Tuple]=None, conf_file=None, nlp_configuration: Optional[Dict]=None).create_engine()`. Registered engine names: `spacy`, `stanza`, `transformers`, `slim`, `no_op`.

- **NoOpNlpEngine(models: List[Dict[str,str]])**
  - Loads nothing and returns empty artifacts (no tokens or lemmas).
  - The LemmaContextAwareEnhancer can then only use the explicit `context=` passed to `analyze()`. It cannot see surrounding words.
  - Best choice when every recognizer is self-contained (regex, deny-list, HF, GLiNER).
- **SlimSpacyNlpEngine(models=None, supported_languages=None, auto_download=True, generic_tokenizer=None)**
  - spaCy with NER disabled: tokens, lemmas, stopwords and punctuation only.
  - Described in the source as intended for "Presidio v3", where NER lives in recognizers.
  - Caveat: `auto_download=True` downloads models from the network. Set it False for air-gapped use.
- **SpacyNlpEngine(models=None [default en_core_web_lg], ner_model_configuration=None)**
  - spaCy NER gives **no real confidence**. Every entity gets `ner_model_configuration.default_score` (0.85).
  - That score is then multiplied by `low_confidence_score_multiplier` (0.4) for `low_score_entity_names`.
- **TransformersNlpEngine(models=None [default {"spacy":"en_core_web_sm","transformers":"obi/deid_roberta_i2b2"}], ner_model_configuration=None)**
  - spaCy is used for tokens and lemmas. The HF NER runs through spacy-huggingface-pipelines, which has stride and aggregation settings.
  - Scores are real HF scores, still subject to the low-score multiplier.
  - The spans are aligned to spaCy tokens with `alignment_mode` (default `expand`, so spans can grow).
  - `conf/transformers.yaml` uses `StanfordAIMI/stanford-deidentifier-base`, maps PATIENT/STAFF/HCW→PERSON, HOSP/HOSPITAL/FACILITY→LOCATION/ORG, ID, AGE, DATE, PHONE…, and lists `ID` as a low-score entity (×0.4).
- **StanzaNlpEngine(models=None, ner_model_configuration=None, download_if_missing=True)**. Caveat: it downloads models if missing.
- `NerModelConfiguration` (pydantic) fields and defaults:
  - `labels_to_ignore`
  - `aggregation_strategy="max"`
  - `stride=14`
  - `alignment_mode="expand"`
  - `default_score=0.85`
  - `model_to_presidio_entity_mapping`
  - `low_score_entity_names`
  - `low_confidence_score_multiplier=0.4`
- Device detection is cuda or cpu only. There is no MPS path in `device_detector`, so on a Mac it runs on CPU unless you build the HF pipeline yourself.
- Docs (customizing_nlp_models.md): "Presidio can currently use one NER model per language via the `NlpEngine`. If multiple are required, consider wrapping NER models as additional recognizers."

## 6. Predefined NER recognizers (predefined_recognizers/ner/)

**HuggingFaceNerRecognizer(LocalRecognizer)**
```python
(supported_entities=None, name="HuggingFaceNerRecognizer", supported_language="en", version="0.0.1",
 context=None, model_name=None, label_mapping=None, threshold=0.3, aggregation_strategy="simple",
 chunk_overlap=40, chunk_size=400, device=None, tokenizer_name=None,
 text_chunker: Optional[BaseTextChunker]=None, label_prefixes=None [B-,I-,U-,L-], **kwargs [ignored+warned])
```
- It calls `transformers.pipeline("token-classification", ...)` directly. It ignores nlp_artifacts and does no spaCy alignment, so offsets come straight from the HF tokenizer.
- Labels: the B-/I-/U-/L- prefix is stripped, then `label_mapping` is applied. `DEFAULT_LABEL_MAPPING` covers CoNLL and Korean labels only, so **supply a clinical mapping**. **Unmapped labels pass through as the raw model label**, and they are kept even when `entities=` does not request them ("discovery").
- Emits the **raw HF pipeline score** (the aggregated softmax for the chosen strategy) as `score`. Results below `threshold` are dropped inside the recognizer, before Presidio thresholds are applied. Set `threshold=0` (or very low) if you want to calibrate or audit downstream.
- It sets `AnalysisExplanation(original_score=score, textual_explanation=...)`. It sets no recognition_metadata; the engine adds the name and id.
- Chunking: `CharacterBasedTextChunker(chunk_size=400, chunk_overlap=40)` by default. For a single chunk it calls the model directly. For multiple chunks it adjusts offsets, then `deduplicate_overlapping_entities(overlap_threshold=0.5)`: sorted by score, an overlap of the same type greater than 0.5×min(len) counts as a duplicate, and the lower-scored one is dropped.
- Chunking is character-based and is not aware of the tokenizer's 512-token limit. Long chunks may be truncated, depending on pipeline settings, so verify that chunk_size fits the model limit.

**GLiNERRecognizer(LocalRecognizer)**
```python
(supported_entities=None, name="GLiNERRecognizer", supported_language="en", version="0.0.1", context=None,
 entity_mapping=None, model_name="urchade/gliner_multi_pii-v1", flat_ner=True, multi_label=False,
 threshold=0.30, map_location=None, text_chunker=None, load_onnx_model=False, onnx_model_file="model.onnx",
 **model_kwargs)
```
- `entity_mapping` = {gliner_label_prompt: PRESIDIO_ENTITY}. It cannot be combined with `supported_entities`. If neither is given, it uses the NerModelConfiguration default mapping.
- The GLiNER labels are the mapping keys, used as zero-shot prompts.
- Emits the raw GLiNER `score`, after GLiNER's own `threshold`.
- Default chunker: character-based, 250 chars with 50 overlap.
- The ONNX option is for older CPUs. Docs: docs/samples/python/gliner.md; install with `pip install 'presidio-analyzer[gliner]'`.

**MedicalNERRecognizer(HuggingFaceNerRecognizer)**. Note the class name is `MedicalNERRecognizer`.
- Constructor: `(model_name="blaze999/Medical-NER", label_mapping=None, supported_entities=None, name="MedicalNERRecognizer", supported_language="en", aggregation_strategy="simple", threshold=0.3, device=None, text_chunker=None)`.
- It detects **clinical concepts, not PHI**: MEDICAL_DISEASE_DISORDER, MEDICATION, THERAPEUTIC_PROCEDURE, CLINICAL_EVENT, BIOLOGICAL_ATTRIBUTE/STRUCTURE, FAMILY_HISTORY, HISTORY.
- At most it is useful as a *negative* signal, for example to suppress false-positive PERSON tags on eponyms or drug names. It is not a PHI detector.

nlp_engine_recognizers/: `SpacyRecognizer`, `StanzaRecognizer` and `TransformersRecognizer` read `nlp_artifacts.entities/scores`. They are added automatically by `registry.add_nlp_recognizer(nlp_engine)`.

chunkers/: `BaseTextChunker` (`chunk`, `predict_with_chunking`, `deduplicate_overlapping_entities`), `CharacterBasedTextChunker(chunk_size=250, chunk_overlap=50, boundary_chars=None)` (keeps word boundaries), and `TextChunkerProvider({"chunker_type":"character", ...})`.

## 7. Context enhancement (context_aware_enhancers/)
```python
LemmaContextAwareEnhancer(context_similarity_factor=0.35, min_score_with_context_similarity=0.4,
                          context_prefix_count=5, context_suffix_count=0,
                          context_matching_mode="substring" | "whole_word")
```
- It applies only to results from recognizers that have a non-empty `context` list. It skips results already flagged `is_score_enhanced_by_context`.
- Surrounding words are the **lemmas** of 5 tokens before and 0 after. They need `nlp_artifacts.tokens` / `lemmas` / `keywords`, which means a spaCy/slim/stanza/transformers engine, not no_op. The request-level `context=` words are added to them.
- On a match: `score = min(1.0, max(score + 0.35, 0.4))`, then `analysis_explanation.set_supportive_context_word(...)` and `set_improved_score(score)`. The analysis explanation then holds `score_context_improvement = new - original`.
- **Caveats:**
  - The default "substring" mode matches `'lic'` inside `'duplicate'`. Prefer `"whole_word"`.
  - Context changes scores before thresholding, so it changes what survives.
  - The enhancer dereferences `result.analysis_explanation` without a None check. Custom recognizers should always attach an AnalysisExplanation.

## 8. Decision process tracing
- `return_decision_process=True` keeps `analysis_explanation` on each returned result, but only for **surviving** results.
- `log_decision_process=True` (constructor) plus `AppTracer(enabled=True)` writes to the logger `"decision_process"` at INFO, in two places:
  - (a) `nlp_artifacts.to_json()`, which **includes the raw token texts and entity texts, i.e. PHI in logs**
  - (b) all results after context enhancement and before thresholding (offsets, scores and explanations; no span text)

  **Do not enable it for real PHI unless the log sink is treated as PHI.** `AppTracer(enabled)` exposes only `trace(request_id, trace_data)`, so you can subclass it to send traces to a secure in-memory sink. That is a cheap way to keep "below-threshold evidence".

## 9. LMRecognizer / llm_utils
- `LMRecognizer(RemoteRecognizer, ABC)(supported_entities, supported_language="en", name=None, version="1.0.0", model_id=None, temperature=None, min_score=0.5, labels_to_ignore=None, enable_generic_consolidation=True)` with abstract `_call_llm`.
- Concrete classes: `LangExtractRecognizer`, `BasicLangExtractRecognizer` and `AzureOpenAILangExtractRecognizer` (third_party/).
- llm_utils covers config loading, prompts and examples (`conf/langextract_prompts/default_pii_phi_prompt.j2`), entity mapping, Azure auth and result filtering/validation.
- These call LLMs: Azure OpenAI by default, or any LangExtract provider. `conf/langextract_config_basic.yaml` also shows a **local Ollama** provider (`http://localhost:11434`), so purely local use is technically possible.
- **Not used for PHI in this project.** Also: `AzureHealthDeidRecognizer` (third_party/ahds_recognizer.py) and the anonymizer's `surrogate_ahds` operator send text to Azure. Avoid both.

## 10. presidio_anonymizer
```python
AnonymizerEngine().anonymize(text: str, analyzer_results: List[RecognizerResult],
    operators: Optional[Dict[str, OperatorConfig]] = None,
    conflict_resolution: ConflictResolutionStrategy = ConflictResolutionStrategy.MERGE_SIMILAR_OR_CONTAINED,
    merge_entities_with_spaces: bool = True) -> EngineResult   # .text, .items[OperatorResult]
add_anonymizer(cls) / remove_anonymizer(cls) / get_anonymizers()
OperatorConfig(operator_name: str, params: Dict = None)
DeanonymizeEngine().deanonymize(text: str, entities: List[OperatorResult], operators: Dict[str, OperatorConfig]) -> EngineResult
BatchAnonymizerEngine, BatchDeanonymizeEngine also exist.
```
- The input results are copied with only start, end, entity_type and score, so explanation and metadata are discarded.
- Operators are keyed by entity_type, with a `"DEFAULT"` fallback (replace → `<ENTITY_TYPE>`). The engine injects `params["entity_type"]`.

Built-in anonymizers:

| Operator | Params | Notes |
|---|---|---|
| replace | `new_value` | Empty value gives `<TYPE>`. |
| redact | none | |
| mask | `masking_char`, `chars_to_mask`, `from_end` | |
| hash | `hash_type` (sha256/sha512), `salt` | **Since 2.2.361 a random 32-byte salt per entity unless you supply `salt` (≥16 bytes)**. Supply a salt for deterministic output. |
| encrypt | `key` | AES-CBC with a **random IV**: reversible with `decrypt`, but not deterministic. |
| keep | none | |
| custom | `lambda` | A callable returning str. Validate no longer calls the lambda, so stateful lambdas are safe (issue #2024). |
| surrogate_ahds | | Azure; avoid. |

Deanonymizers: `decrypt`, `deanonymize_keep`.

**Deterministic reversible tokenization: yes, via a custom operator.** Two routes:
- a `custom` lambda, or
- a subclass of `Operator` (with `operator_type()` returning Anonymize) that keeps a client-side value→token map, or computes a keyed HMAC token plus a vault. See docs/samples/python/pseudonymization.ipynb (`InstanceCounterAnonymizer` plus a matching deanonymizer) and `example_custom_lambda_anonymizer.py`.

Presidio itself holds no state ("Presidio does not store or maintain stateful sessions"). A deterministic, non-reversible option is `hash` with a fixed secret salt, which works like a keyed hash; the docs warn to discard or secure the salt.

**Conflict resolution** (source plus anonymizer/index.md):
1. Results are sorted. Then **same-type intersecting results are merged** into their union span, keeping the max score.
2. A result is dropped if another result has identical indices and an equal or higher score, or if it is contained in another result of **any type**. "Contained" wins even with a lower score.
3. With the default MERGE_SIMILAR_OR_CONTAINED, **partially overlapping results of different types are both kept** and replaced individually ("I'm <PERSON><LOCATION>."). `TextReplaceBuilder` replaces from the end with `min(end, last_replacement_index)`, so the union of characters is covered and the overlap does not leak.
4. `REMOVE_INTERSECTIONS` trims the lower-scored span. `NONE` is also defined.
5. `merge_entities_with_spaces=True` joins adjacent same-type spans separated only by spaces.

## 11. Official examples / docs to follow
- Transformers/HF with Presidio:
  - docs/analyzer/nlp_engines/transformers.md (NLP-engine route plus "Using a transformers model as an EntityRecognizer")
  - docs/samples/python/transformers_recognizer/ (index.md, transformer_recognizer.py, configuration.py)
  - HuggingFaceNerRecognizer YAML example in docs/analyzer/recognizer_registry_provider.md
- GLiNER: docs/samples/python/gliner.md, plus `conf/slim.yaml`.
- YAML / no-code: docs/analyzer/analyzer_engine_provider.md, recognizer_registry_provider.md, samples/python/no_code_config.ipynb; conf/*.yaml.
- Custom recognizers: docs/analyzer/adding_recognizers.md (PatternRecognizer, EntityRecognizer subclass, RemoteRecognizer, ad-hoc recognizers, reading from YAML), developing_recognizers.md, samples/python/customizing_presidio_analyzer.ipynb.
- Known values / patient-specific lists: samples/python/"Anonymizing known values.ipynb".
- External detectors: adding_recognizers.md#creating-a-remote-recognizer, samples/python/example_remote_recognizer.py, integrating_with_external_services.ipynb (Azure Text Analytics), span_marker_recognizer.py, flair_recognizer.py.
- Pseudonymization: samples/python/pseudonymization.ipynb, encrypt_decrypt.ipynb.
- Evaluation: docs/evaluation/index.md, which points to the presidio-research repo (`github.com/data-privacy-stack/presidio-research`) and recommends F2.

## Practical implications for a minimal local engine
1. Use `NoOpNlpEngine`, or `SlimSpacyNlpEngine(auto_download=False)` if you want lemma context. Add:
   - pattern recognizers (predefined ones plus custom MRN, accession and phone patterns)
   - per-request `ad_hoc_recognizers` deny-lists for patient-specific identifiers
   - **one** `HuggingFaceNerRecognizer` with a clinical de-id model (e.g., obi/deid_roberta_i2b2 or StanfordAIMI/stanford-deidentifier-base), a clinical `label_mapping`, `threshold=0`, and chunk_size checked against 512 tokens
2. Do not pass request-level `score_threshold` if you rely on per-recognizer thresholds. Set thresholds per recognizer and entity.
3. Capture raw evidence before engine dedup and thresholding, with a custom AppTracer sink or by running recognizers directly. Use `return_decision_process=True` for provenance of the surviving results.
4. Fail closed on config load. Disable auto-downloads. Never enable `log_decision_process` with a stdout or file handler.
