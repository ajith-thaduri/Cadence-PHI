# Industry-grade TEVV / evaluation harness notes for a local PHI detection + de-identification engine

Compiled 2026-10-06. Scope: standards, regulator guidance, and vendor documentation, not de-id papers. The literature (hybrid systems, ASR PHI, leakage metrics, calibration, cross-site drop, datasets) is in `literature_notes.md`, and the Presidio API is in `presidio_api_notes.md`. This file cites them as **[LIT §x.y]** and **[PRES §x]** and does not repeat them.

Evidence tags: **[V]** = I fetched and read the page or PDF. **[S]** = search snippet, secondary summary, or my own knowledge of a standard I could not fetch (several are paywalled ISO documents, and hhs.gov returned 403). Treat [S] items as pointers to verify before quoting them in compliance documents.

---

## 0. Bottom line for the harness (most decision-relevant)

1. **Make recall on direct identifiers the release gate. Do not use F1.** Microsoft's de-id service states that its *primary release criterion is recall* (goal >95%, entity-level, fuzzy match), with precision secondary [V Azure TN]. Report recall with a confidence interval, and gate on the **lower bound**, per entity class and per modality.
2. **Score the same predictions three ways:** (a) binary token-level PHI recall (leak view), (b) entity-level strict and relaxed per type, as in i2b2 2014 [V], and (c) document-level "fully sanitized" rate plus entity "all-mentions-masked" recall [LIT §4.4]. Use presidio-research's Span/Token evaluators as a cross-check [V].
3. **Test the pipeline as a system, not just the model.** That means config load, chunking, reconciliation, tokenization, and the vault. NIST SP 800-188 §5.3 asks you to verify *correctness, containment (no leaks "in expected or unexpected ways"), usability, efficiency, scalability, repeatability, transparency* of de-id software [V].
4. **Fail closed, and test that it does.** Use NIST SP 800-53 SC-24 (fail in a known state) and SI-17 (fail-safe procedures) as the control basis [V]. Required harness tests include a model load failure, a timeout, an oversized document, a bad config, and an unavailable vault. Each must produce *no LLM call*, or a fully masked fallback, and never pass-through.
5. **Treat config as a versioned artifact.** It should be schema-validated with Pydantic (Presidio already validates its YAML with pydantic [PRES §2]), and every output and eval report should carry a content hash of the config, the model, and the gold set. AWS returns `ModelVersion` on every DetectPHI response [V]. Google DLP templates are explicitly *not* versioned, only create/update timestamps [V], so do not copy that gap.
6. **Reversible tokens must not be derived from PHI unless a protected secret key is used.** Under 45 CFR 164.514(c) the re-identification code must be "not derived from or related to information about the individual" and its mechanism must not be disclosed [V]. Use a random token plus an encrypted vault, or a keyed transform such as FF1 or HMAC with a managed key. A plain hash of the value is not acceptable. Note: FF1 is in SP 800-38G Rev.1, still a 2nd public draft (Feb 2025). FF3 and FF3-1 were removed, and the minimum domain is 1,000,000 [V].
7. **Synthetic and fake PHI is fine for unit and behavioral tests, but release evidence needs real local data.** Microsoft: "Small datasets, synthetic data, and tests that don't reflect your end-to-end scenario are unlikely to sufficiently represent your production performance" [V]. See also [LIT §6.1].
8. **No standard prescribes a numeric recall threshold for de-id.** HHS Expert Determination sets no number ("very small" risk) [V 45 CFR]. HITRUST requires *you* to define thresholds [S]. Thresholds are an organizational risk decision and must be documented with their rationale. This is unsettled industry practice.

---

## 1. TEVV / AI assurance frameworks

**NIST AI RMF 1.0 (NIST AI 100-1, Jan 2023), MEASURE function, Playbook** [V airc.nist.gov]. The RMF is "being revised as part of the White House AI Action Plan" [V nist.gov].
- MEASURE 2.1: "Test sets, metrics, and details about the tools used during TEVV are documented", for repeatability. → The harness emits a machine-readable run manifest: gold set ID and hash, config hash, model hash, code commit, metric definitions, and tool versions.
- MEASURE 2.3: performance "demonstrated for conditions similar to deployment setting(s)". → Use separate strata for care-management call ASR, typed notes, and outbound text, and never pool them into a headline number.
- MEASURE 2.4: production monitoring for drift against the pre-deployment baseline. → Define the baseline numbers in the harness report so that production monitoring has something to compare against.
- MEASURE 2.5: document "limitations of the generalizability". → Add a "known failure modes" section to the model/system card, fed by the error-analysis outputs.
- MEASURE 2.6: residual negative risk does not exceed tolerance, and the system can "fail safely, particularly if made to operate beyond its knowledge limits". → Out-of-scope input tests: non-English, very long input, binary or garbage text, unknown note types.
- MEASURE 2.7: security and red-teaming. → Adversarial PHI tests: obfuscated SSNs, spelled-out numbers, PHI split across ASR turns, and prompt-injection text that asks the shield to skip.
- MEASURE 2.8: audit logs; MEASURE 2.10: privacy risk documented. MEASURE 1.3: **independent assessors** ("internal experts not involved in development"). → Have someone other than the builders adjudicate the release gold set, and have them sign the release report.

**NIST AI 600-1 GenAI Profile (Jul 2024)** [S]. Data Privacy is one of its 12 GenAI risks, including leakage of user inputs to the model provider. → Its relevance here is that it frames the de-id shield as a control on *prompt-side* privacy risk for the downstream LLM. Cite it in the risk register.

**GMLP guiding principles.** FDA, Health Canada and MHRA published these in 2021. IMDRF finalized them as N88 in Jan 2025 [V FDA page for dates; principle list S]. The principles that bear on this harness:
- P4: training sets independent of test sets. → Hash-based contamination checks between any fine-tune or calibration data and the gold test set, done at document and member level so the same patient never sits on both sides.
- P3: data representative of the intended population. → Stratify by line of business, call type, speaker role, and accent if available.
- P5: reference datasets built with the best available methods. → Gold standard needs written annotation guidelines, double annotation, and adjudication.
- P7: performance of the human-AI team. → If humans review flagged items, measure the reviewer's catch rate too.
- P8: testing under clinically relevant conditions.
- P10: deployed models are monitored and re-training risk is managed. → Define re-validation triggers (see §10).

**ISO/IEC 42001:2023 (AI management system)** [S]. 38 Annex A controls across A.2–A.10, selected through a Statement of Applicability, covering the life cycle, data for AI, and V&V records. → Keep the harness outputs as auditable records: run manifests, sign-offs, and change tickets. **ISO/IEC 25059:2023** [S] adds AI-specific quality sub-characteristics to the SQuaRE/25010 model, such as functional correctness, robustness, user controllability, and transparency. → Use it as the taxonomy for non-functional test categories.

**CHAI.** Assurance Standards Guide (draft, June 2024) [S]. Applied Model Card v0.1 (draft, Nov 2024) [V PDF]. The card fields are Release stage/version, Uses and Directions (intended use, users, *cautioned out-of-scope settings*), Warnings (known risks, biases, clinical risk level), Trust Ingredients (model type, input data source, development data characterization, ongoing maintenance, security/compliance), and **Key Metrics in three columns**: Usefulness/Usability/Efficacy, Fairness/Equity, Safety/Reliability. Each column has *Goal, Result, Interpretation, Test Type, Testing Data Description, Validation Process and Justification*. CHAI recommends issuing a revised card tied to the software revision when uses change, a new risk is found, key metrics change significantly, or there is a structural change in development or training [V].
- → Generate an applied-card-style system card from every release-candidate harness run. Safety/Reliability = leak metrics. Fairness = recall by name origin, accent, and member subgroup. Usefulness = over-redaction rate and downstream task impact.

**ONC HTI-1 DSI source attributes** [S]. These are the 31 predictive-DSI attributes for certified health IT. The HTI-5 proposed rule (comment period closed Feb 27, 2026) would **remove the AI "model card" requirements** [S healthit.gov]. → This does not legally apply to an internal de-id shield and is in regulatory flux. Use CHAI or model cards voluntarily rather than citing HTI-1 as a requirement.

---

## 2. De-identification standards

**HIPAA Privacy Rule, 45 CFR 164.514** [V Cornell LII]. The HHS OCR 2012 guidance page returned 403, so the FAQ details below are [S].
- Safe Harbor (b)(2): remove all 18 identifiers. These include geographic subdivisions smaller than a state (the first 3 ZIP digits are allowed only if the area contains more than 20,000 people), "all elements of dates (except year)" for individual-related dates, ages over 89 (collapse to "90 or older"), and "any other unique identifying number, characteristic, or code". You must also have **no actual knowledge** that the remaining information could identify someone.
- Expert Determination (b)(1): "the risk is very small" that an anticipated recipient could identify the individual, using reasonably available information. The method and results must be documented. OCR's FAQ adds that no fixed numeric threshold exists and that determinations can be time-limited [S].
- 164.514(c) re-identification codes: not derived from individual information and not disclosed [V].
- **Implications.** (1) This engine sends data to an LLM *under a BAA*. The output is PHI-minimized rather than "de-identified" in the legal sense, unless you pursue Expert Determination. Say so explicitly in the system card, and do not claim Safe Harbor. (2) Even so, keep the 18 categories plus HIPAA-specific edge cases (ages >89, ZIP3 handling, date elements such as "admitted last Tuesday") as a **coverage matrix** in the test suite. Every Safe Harbor category needs at least one MFT test suite. (3) Tokenization design must respect 164.514(c) (see §7).

**NIST SP 800-188, *De-Identifying Government Datasets: Techniques and Governance* (Sep 2023)** [V full PDF]
- Unstructured text: "Finding such identifiers invariably requires domain-specific knowledge" (§4.3).
- Pseudonyms: a lookup table or a repeatable transform must be "highly protected". With a lookup table "the pseudonym must be randomly assigned". "A significant risk of using a repeatable transformation is that a data intruder may be able to determine the transformation". It also warns about frequency attacks when last names are pseudonymized. The document cites Carrell's realistic-surrogate work favorably.
- Test data, §4.4.2–4.4.3: make test data "conspicuously non-natural ('FIRSTNAME1 LASTNAME2' rather than 'JOHN SMITH')" so that it is not confused with real data. Realistic test data is acceptable if it is not derived from confidential data. → **Use two kinds of synthetic data.** Conspicuous fakes go in CI fixtures and logs, so a leak alert can never be confused with real PHI. Realistic fakes go in behavioral (CheckList) tests.
- Validating privacy, §4.6.2: critically evaluate software defaults, run motivated-intruder tests (pointing to the UK ICO guidance), and report results for vulnerable populations.
- Software evaluation, §5.3: Correctness, **Containment**, Usability, Efficiency, Scalability (N→100N), **Repeatability** ("if the tool is run twice on the same dataset, are the results similar?"), and Transparency. §5.2: tools "should be validated for the specific kinds of data that the agency intends to use", and some tools handle only some modalities.
- Governance: a Disclosure Review Board and the Five Safes framework (§3.5).

**NIST IR 8053, *De-Identification of Personal Information* (Garfinkel, Oct 2015)** [S]. Background survey covering text de-id error rates and HIPS. Superseded in practice by SP 800-188.

**ISO/IEC 20889:2018** [S]. Terminology and classification of de-id techniques (masking, pseudonymization, generalization, and so on). → Use its vocabulary in the policy config ("transformation: pseudonymize | mask | generalize | suppress").
**ISO 25237:2017, Health informatics: Pseudonymization** [S]. Covers the pseudonymization service methodology, *controlled re-identification* policy, and the trustworthiness of pseudonymization service operators. It is under revision (ISO/DIS 25237, iso.org/standard/86409). → The vault, de-tokenization, and the roles allowed to re-identify should follow its "controlled re-identification" model: a documented purpose, an authorized role, and an audit log.

**HITRUST De-Identification Framework (2015)** [S, secondary only]. 4 governance criteria plus 12 methodology criteria. The governance criteria are governance, documentation, identification of parties, and scrutiny: partial review at least annually and a deeper review every 3 years. The methodology criteria include **defining re-identification risk thresholds** and **objective measurement against thresholds**. → Supports an annual re-validation cadence and documented thresholds.

**UK ICO anonymisation and pseudonymisation guidance (final, 2025)** [S]. Covers the "spectrum of identifiability", the **motivated intruder test** (a reasonably competent person with internet resources who makes enquiries), and the accountability and DPIA requirements. → Optional but credible: run a small motivated-intruder exercise on post-shield transcripts (§4 metric "residual risk"). Carrell 2020 is the clinical precedent [LIT §4.2].

**Residual-risk concept.** Every standard assumes residual identifiers remain. SP 800-188 recommends surrogates to hide leaks. Microsoft recommends surrogation over redaction and **warns that consistent surrogation over large batches degrades privacy** [V Azure TN]. This matches [LIT §4.3, Osborne 2025]. → If the LLM sees realistic surrogates, scope consistency to the conversation or member, not globally, and test the leakage effect.

---

## 3. How commercial de-id services document behavior and evaluation

| Vendor | Configurability | Confidence | Versioning | Evaluation disclosure |
|---|---|---|---|---|
| **Google Sensitive Data Protection (DLP) / Healthcare API** [V] | Declarative `inspectConfig`: infoTypes, `customInfoTypes` (regex, dictionary, stored), `ruleSet` of **hotwordRule** (`hotwordRegex`, `proximity.windowBefore/After`, `likelihoodAdjustment` fixed or relative) and **exclusionRule** (dictionary, regex, `excludeInfoTypes`, `excludeByHotword`; `matchingType` FULL/PARTIAL/INVERSE), `minLikelihood`. Separate `deidentifyConfig` (transformations). Stored **inspect/de-identify templates** "decouple configuration ... from the implementation" | Ordinal 5-level **likelihood** (VERY_UNLIKELY…VERY_LIKELY), not a probability | InfoType has an optional `version` field. Templates are **not versioned** (create/update time only). Healthcare API: results "may differ ... due to ... heuristic-based algorithms" | No public accuracy numbers |
| **AWS Comprehend Medical DetectPHI** [V] | Essentially none: a fixed set of 9 entity types (AGE, DATE, NAME, PHONE_OR_FAX, EMAIL, ID, URL, ADDRESS, PROFESSION), which "don't map 1:1" to Safe Harbor. Max 20,000 bytes per call | Per-entity `Score`. "Identify the right confidence threshold for your use case ... it may be better to use a low confidence threshold" and "use additional human review" for compliance uses | **`ModelVersion` (X.X.X) returned in every response** "to track the model used for a particular batch" | None public |
| **Azure Health Data Services de-id** [V overview + Transparency Note] | Operations TAG / REDACT / SURROGATE; 27 entity types; consistent surrogation within a batch job. Limits: 50 KB per request, 2 MB per document | — | — | **Release criterion = recall, goal >95%**, measured entity-level with fuzzy span match on academic and internal sets. Precision secondary. Also measures how often surrogates keep the same type. Warns: **not for real-time/transcription use where only pre-context is available**. Misspelling and structured text hurt. Limit batch size for consistent surrogation |
| **Microsoft Presidio + presidio-research** [V repo docs] | YAML registry (see [PRES §2]) | Raw recognizer scores (uncalibrated) | Library semver | Token evaluator and span evaluator (IoU threshold, char- or token-based), per-entity and global "PII vs not" P/R/**Fβ (β=2 recommended)**, confusion matrix, error objects, `CanonicalMapper` for entity mapping. Hierarchy projection rule: "the gold annotation decides the granularity" (ADR-003, 2026). Active repo, pushed 2026-10-01 |
| **John Snow Labs Healthcare NLP** [S API docs] | `DeIdentification` annotator: mask vs obfuscate, `obfuscateRefSource` (file, faker, both), `consistentObfuscation` (Levenshtein-similar entities get the same fake), `consistentAcrossNameParts`, date shifting | Per-chunk NER confidence | Versioned pretrained pipelines | Vendor-published benchmarks (see [LIT §1]) |
| **Philter-UCSF** [V README] | JSON filter config (regex, whitelist and blacklist sets, ordered filters) | None (rule-based plus whitelist) | Git | Built-in eval mode: recall, precision, F, per category, false-negative listing, unigram/bigram PHI frequency table. `--prod` disables eval |
| **Private AI (Limina)** [S docs] | ENABLE/DISABLE entity selectors over 50+ types and groups, allow/block lists, custom regex, `accuracy` mode (for example "high") | — | Versioned container releases | — |
| **Tonic Textual** [S docs] | Per-entity handling: Redact / Synthesize / Ignore. Custom regex or trained custom models. `custom_entity_ranking_modes`: Prioritized (custom wins) vs Standard (confidence decides) | Confidence used for conflict resolution | — | — |
| **AWS Transcribe PII redaction** [V] | `PiiEntityTypes`. Streaming can *identify without redacting*. Redaction is at segment level, after the segment completes. Call Analytics also redacts audio | — | — | "Amazon Transcribe may not identify and remove all instances", and "does not meet the requirements for de-identification under ... HIPAA" |

**Patterns worth copying.**
1. **Separate the inspect config (what to detect) from the de-identify config (how to transform).** This is Google's design and maps directly onto our recognizers/thresholds vs policy/transform split.
2. **Use exclusion rules and hotword rules as first-class declarative objects**, not code. Presidio already has `context` and deny lists. Add an explicit allow-list/exclusion object (medical-term safe list [LIT §1]) with matching types.
3. **Return detector and model versions in every response**, as AWS does, plus a config hash.
4. **Precedence rules for conflicts should be explicit config**, as with Tonic's ranking modes. Our reconciliation layer's precedence (registry > rules > NER?) belongs in config and needs its own tests.
5. **Every vendor disclaims completeness and recommends human review and local evaluation** (AWS, Azure, Transcribe). Industry practice therefore puts the burden of local validation on the deployer, which is our harness.
6. **Scores are not comparable across sources.** Google uses ordinal likelihood, AWS a 0–1 score, and Presidio uncalibrated per-recognizer constants. → The reconciliation layer should map every source to a common calibrated or ordinal scale, and the harness should test that mapping (reliability diagram per source; see [LIT §5]).

---

## 4. Evaluation methodology for de-id

**i2b2/UTHealth 2014 Track 1** (Stubbs, Kotfila, Uzuner, JBI 2015) [V PMC4989908]
- Entity-based ("instance-based") evaluation requires **exact begin and end offsets**. Token-based evaluation is more lenient. The paper's own example is a split "Rayna" + "De Angelis", which counts as correct by tokens and wrong by entity.
- Three label sets: **i2b2-PHI** (fine-grained), **HIPAA-PHI** (the 18-identifier subset), and **binary PHI vs non-PHI**.
- Primary ranking metric: micro-averaged strict entity F1 over i2b2-PHI. Significance by **approximate randomization**, N=9,999, α=0.1.
- Corpus (Stubbs & Uzuner 2015, PMC4978170 [V]): **double annotation by two independent annotators plus adjudication by an author**. IAA token micro-F1 0.930, entity micro-F1 0.895.
- Presidio-research matching is IoU-based with a configurable threshold [V]. Azure uses "fuzzy matching" [V].

**Recommended metric stack.** Each line says what it implies for the harness.
1. **Binary token-level PHI recall (leak rate = 1 − recall)**: the primary safety metric [LIT §4.5]. Compute over *characters* as well as tokens, because tokenization differs between ASR and notes.
2. **Per-category strict and relaxed entity recall and precision**, with relaxed = any overlap or IoU ≥ 0.5. Report HIPAA-18 and fine-grained label sets separately, and keep direct and quasi identifiers separate [LIT §4.4].
3. **"All-mentions-masked" entity recall** [LIT §4.4] and **document-level fully-sanitized rate**: the share of documents with zero leaked direct-identifier characters. The document rate is the easiest metric for business stakeholders to understand.
4. **F2** as a secondary summary only (Presidio and Philter convention).
5. **Over-redaction / utility**: precision, the fraction of clinical terms masked (from a curated list of medications, conditions and procedures), and optionally downstream LLM task quality with and without the shield [LIT §4.6].
6. **Tokenization correctness**: round-trip exactness (detokenize(tokenize(x)) == x on PHI spans), consistency within scope, no collisions, and no token that encodes PHI.

**Statistics for small test sets.**
- **Use a document- or member-level cluster bootstrap**, not a token-level one. Tokens within a document are correlated. Resample documents, at least 1,000 replicates, and report percentile 95% CIs [S, standard practice].
- **When a category has zero misses, apply the rule of three.** With 0 misses in n gold mentions, the 95% upper bound on the miss rate is about 3/n (Hanley & Lippman-Hand, JAMA 1983) [S]. So 100% recall on 50 SSNs only shows a miss rate below about 6%. → Use this to **size the gold set**. To claim a miss rate under 1% with 95% confidence for a category, you need about 300 mentions of that category with zero misses. Use Clopper-Pearson or Wilson intervals when misses are above zero.
- **Run paired comparisons when choosing between candidate versions.** Use approximate randomization or a paired bootstrap on the same documents (i2b2 practice). See Dror et al., ACL 2018, for the test-selection protocol [S].
- **Report every stratum with its n.** Hide or flag any metric with fewer than about 30 support mentions, rather than showing a misleadingly precise number.

**Stratification.** Strata are entity type × modality (ASR call, typed note, SMS/email) × document type × speaker role (member vs care manager) × length bucket × ASR-quality bucket (if WER is available). Also stratify by name-origin category, which is the fairness view that CHAI's card asks for.

**Gold-standard process** (i2b2, GMLP P5).
- Write annotation guidelines with decision rules for PHI edge cases: provider names, relative dates, ages >89, facility names, spelled-out numbers in ASR, and partial or fragmentary IDs.
- Double-annotate at least a subset. Report IAA as pairwise F1 (the i2b2 convention). Cohen's κ is ill-defined for span tasks because the number of negatives is unbounded.
- Adjudicate disagreements with a third person. **Version the gold set** with a content hash and changelog. Never edit gold to match a system output without an adjudication record.

**Behavioral tests (CheckList, Ribeiro et al., ACL 2020)** [V abstract]. These run alongside the gold-set metrics:
- **MFT** (minimum functionality): template-generated PHI in every Safe Harbor category and format. Examples: phone formats, SSN with and without dashes, MRN patterns, dates in 15 formats, "ninety-two year old", emails, URLs, IPs, and member-ID formats from the registry.
- **INV** (invariance): masking must not change under case changes (ASR lowercasing), punctuation removal, filler words ("um"), disfluencies, line breaks, Unicode homoglyphs or zero-width characters, swapping the name for another of different origin, or moving the PHI to another position (start or end of a chunk, across a 512-token chunk boundary).
- **DIR** (directional): adding a hotword ("MRN:", "DOB") should not decrease the detection score. Removing context should not raise a clinical term into PHI.
- Generate fakes with Faker or presidio-research's generator. Follow SP 800-188: use conspicuous fakes in logs.

---

## 5. ASR / conversational PHI

- **Vendors measure little here and disclaim heavily.** AWS Transcribe redaction "does not meet ... HIPAA" de-id requirements and redacts at the segment level after the segment completes [V]. Azure de-id explicitly does not recommend real-time transcription use because it needs post-context [V]. Call Analytics redacts audio by timestamp [S]. None of them publish PHI recall on speech.
- The literature [LIT §3] shows PHI-word WER of about 42%, recall collapsing to 0.37 for i2b2-trained NER on conversations, and WER not tracking entity distortion.
- **What to measure:**
  1. **End-to-end PHI recall on the ASR output**, with gold annotated on the ASR text itself, not projected from the reference transcript. Misrecognized PHI is still PHI. "Jon Smyth" for "John Smith" must be masked.
  2. **Entity survival.** Annotate on the reference transcript and project to the ASR hypothesis by alignment. Then split each miss into an ASR-caused miss (the entity is garbled) or a detector-caused miss, as in the Cohn 2019 error decomposition.
  3. **Cross-turn and split PHI.** Examples: a number dictated over several turns ("four five six ... seven eight"), spelled names ("S-M-I-T-H"), and the agent repeating the member's DOB back.
  4. **Streaming vs batch parity** if any streaming path exists. Chunk-boundary INV tests cover part of this.
- **Treat speech as its own stratum with its own thresholds**, per [LIT §5] calibration notes. Never let typed-note results stand in for ASR results.

---

## 6. ML testing and MLOps production readiness

**ML Test Score (Breck, Cai, Nielsen, Salib, Sculley; IEEE BigData 2017)** [V abstract]. 28 tests in 4 sections: data/features, model development, infrastructure, monitoring. Manual = ½ point, automated = 1 point, and the final score is the **minimum** across sections. → Adopt the "min across sections" idea for the release gate, so a great model with no monitoring fails. Tests that matter here:
- The model is tested against a simple baseline (rules-only Presidio).
- **Model quality is checked before serving**, with an automatic canary/golden-set check at startup.
- Training and serving use the same code path.
- Config is reviewed and tested ("Hidden Technical Debt in ML Systems", Sculley et al., NeurIPS 2015 [S], calls configuration debt a major risk).
- Model staleness, numerical stability, and prediction-quality regressions are monitored.

**Model cards (Mitchell et al., FAT\* 2019) and datasheets (Gebru et al., CACM 2021)** [S]. → System card generated from the harness. Datasheet for each gold set: provenance, consent/BAA basis, annotation process, IAA, known gaps, retention date.

**Data versioning and contamination.**
- Content-hash every gold file (SHA-256) and use a manifest: DVC-style, or a plain JSON manifest committed to git without the PHI.
- Before any calibration or fine-tune, assert that no document or member hash overlaps with the test set.
- Freeze a **locked holdout** that is evaluated only for release decisions. Use a separate dev set for tuning thresholds, so the thresholds are not overfit to the release gate.

**Regression baselines and golden sets.**
- Store the previous release's per-document predictions. On every change, diff them and list *newly missed* gold spans. Any new miss in a direct-identifier category fails CI, even if aggregate recall went up.
- Pin model weights by hash. Presidio's auto-download must be off [PRES §practical].

**Shadow and canary deployment.** Run a new detector version in shadow on live traffic. Without gold labels, compare the **disagreement rate** with production and route disagreements to human review. Industry practice for de-id shadowing is not documented by vendors; this pattern is borrowed from general MLOps [S].

**Production monitoring.** No vendor documents this for de-id, so treat it as an emerging practice.
- **Detection-rate monitoring**: entities per 1k characters by type and modality, with control-chart alerts. A sudden drop in NAME detections per call suggests ASR or model drift. A spike suggests over-redaction.
- **Sentinel/canary records**: periodically inject synthetic transcripts with known conspicuous PHI (SP 800-188 style "FIRSTNAME1") through the full production path, and assert that the downstream LLM payload contains none of them. This is the only end-to-end leak check that needs no real-PHI labels.
- **Post-shield residual scanner**: run a second, independent, high-recall detector (for example regex-only for SSN, phone, email, and registry exact-match) on the outbound payload. Any hit blocks the payload and alerts. This acts as a cheap "second line of defense".
- **Human review sampling**: a stratified random sample of shielded outputs reviewed by trained staff each week. Size it with the rule of three. For example, 300 documents with no leaks shows a document leak rate under 1% with 95% confidence. AWS, Azure and Transcribe all recommend human review [V].

---

## 7. Privacy and security requirements for a harness that handles PHI

**HIPAA Security Rule, 45 CFR 164.312 (current law)** [V]. The Jan 2025 NPRM, which would make encryption and MFA required, is still not final; HHS's agenda puts final action at July 2027 or later [S].
- Access control: unique user ID (required); emergency access (required); automatic logoff and encryption/decryption (addressable).
- Audit controls; integrity (a mechanism to corroborate that data has not been improperly altered); person/entity authentication; transmission security (integrity, encryption).
- **Harness implications:**
  - Gold sets and eval outputs that contain PHI live only in an encrypted, access-controlled store, readable by named eval roles.
  - The harness writes an audit log of who ran which eval on which dataset hash.
  - Reports and CI logs contain **no PHI**: only counts, IDs, offsets, hashes, entity types, and *fake* exemplars.
  - Error-analysis views that show real context are role-gated and not persisted to CI artifacts.

**NIST SP 800-53 Rev.5 controls to map** (SC-24 [V], SI-11 [V], SI-17 [V]; others [S]):
- **AC-3/AC-6** least privilege for the vault and gold data.
- **AU-2/AU-3/AU-9/AU-11** auditable events, content, protection, and retention.
- **SC-8** transmission and **SC-28** storage confidentiality.
- **SC-12/SC-13** key establishment and approved crypto (vault keys).
- **SC-24** fail in known state.
- **SI-11** error messages must not reveal exploitable information, so no PHI in exceptions or stack traces.
- **SI-17** fail-safe procedures.
- **SI-7** software and firmware integrity (model and config hashes).
- **SI-12** information retention.
- **CM-3/CM-5** configuration change control.
- **SA-11** developer testing.
- → Write a control-to-test traceability table: each control is evidenced by a named harness test or artifact.

**Logging without PHI.**
- Never log raw text, recognizer `textual_explanation`, or Presidio's decision process to stdout or files [PRES §practical].
- Log span offsets, types, scores, and a **keyed** hash of the value if you need correlation. Unkeyed hashes of low-entropy PHI such as DOB, phone, or SSN can be brute-forced.
- Add a CI test that greps all log and report outputs for the canary PHI values and fails if any appear.

**Reversible tokenization and keys.**
- 164.514(c): the re-identification code is not derived from PHI, and its mechanism is not disclosed [V]. SP 800-188: lookup-table pseudonyms "must be randomly assigned", and the table or key "must be highly protected" [V].
- **Option A, preferred:** a random token plus an encrypted vault mapping, scoped per conversation or member, with a TTL.
- **Option B:** keyed FF1 FPE or HMAC under a KMS-managed key. FF1 is in SP 800-38G Rev.1 2PD (Feb 2025, not final), which removes FF3 and FF3-1 and requires a domain of at least 10^6 [V]. Small domains such as 2-digit ages or day-of-month are therefore not FPE-eligible.
- Key lifecycle follows NIST SP 800-57 Part 1 Rev.5 [S]: cryptoperiods, rotation, separation of the token-vault key from the log-hash key, and destruction.
- **Harness tests:**
  - Vault entries are encrypted at rest.
  - De-tokenization requires the role and is audited.
  - Rotation does not break in-flight conversations.
  - Expired tokens fail closed: no plaintext is returned.
  - Tokens are not guessable or enumerable.
  - The same value in different scopes gets different tokens, unless policy says otherwise.

**Minimum necessary, retention, and disposal.** Keep eval data for the shortest workable period. Record a retention date in each gold-set datasheet. Securely delete expired runs. Prefer annotating a *minimal* real sample and covering everything else with synthetic CheckList suites (SP 800-188 §4.4).

---

## 8. Performance and reliability testing

- **SLOs as latency percentiles (p50/p95/p99), not means** (Google SRE book practice [S]). Measure per stage (rules, registry, NER, reconciliation, tokenization) and end-to-end. Bucket by input length, because transformer cost is roughly linear in the number of chunks.
- **Throughput and load.** Run a sustained-concurrency test at the expected peak and twice the peak. Measure queueing and memory (RSS), and watch for leaks over a soak test of at least an hour.
- **Cold start.** Measure model load time. The service must **not accept traffic until the model and config are loaded and a startup self-test on canary text passes** (ML Test Score "check before serving").
- **Long documents and chunking.** Use limits from Azure (50 KB per request) and AWS (20 KB per call) [V] as reference points. Test documents at the 512-token boundary, PHI straddling chunk boundaries, and inputs up to the maximum size. Over the maximum size, reject or split. **Never truncate silently and forward the rest unshielded.**
- **Fail-closed policy** (SC-24/SI-17 [V]; SP 800-188 "containment" [V]). On any detector exception or timeout, the shield either (a) blocks the LLM call and returns an error, or (b) applies a maximally conservative fallback (for example rules plus registry-only masking plus a flag) *if policy allows*. This is a policy decision, recorded in config. Fault-injection tests must cover each failure point. I found no vendor or standard that prescribes "fail-closed for redaction" in so many words, so cite the generic controls.
- **Determinism and reproducibility** (SP 800-188 §5.3 "Repeatability").
  - Run the same input twice and get identical spans. Pin seeds, use deterministic torch flags, and fix thread counts where needed. Test CPU vs GPU drift if both are used.
  - Surrogate or token generation is either deterministic within scope by design, or random with the mapping persisted. Test whichever is chosen.
- **Timeouts.** Per-stage budgets with an overall deadline. A timeout follows the fail-closed path. Test with injected slow recognizers.

---

## 9. Configuration architecture

**Evidence base.**
- Google separates inspectConfig from deidentifyConfig and uses stored templates [V].
- Presidio's AnalyzerEngineProvider accepts one combined file or three separate ones (analyzer, nlp, registry), validated with pydantic. **It silently falls back to defaults if the file is missing** [PRES §2], so we need our own fail-closed loader.
- Hydra composition: config groups with "last one wins" merge semantics, so later entries in the defaults list override earlier ones [V].
- Kustomize-style base plus overlays [S].

**Recommended practices.**
1. **Separate policy from mechanism.**
   - *Mechanism*: recognizers, patterns, the model, label mapping, chunking.
   - *Policy*: which entity types are in scope, the per-entity, per-modality threshold, the transform per entity (token, surrogate, mask, generalize), and the failure mode.
   - Compliance or privacy owners review the policy files. Engineers own the mechanism files.
2. **Use one canonical entity taxonomy file.** List the internal entity IDs, their HIPAA-18 mapping, direct vs quasi class, and risk tier, along with the mapping tables from each source (OBI labels, Presidio types, registry fields). Every other file references these IDs, and the schema validator rejects unknown IDs.
3. **Validate the schema strictly.** Use Pydantic models with `extra="forbid"`. Unknown keys, missing entities, or thresholds outside [0,1] stop startup.
4. **Resolve layers in a fixed order**: base, then modality overlay (asr / notes), then environment overlay (dev / test / prod). Allow-list the keys an environment overlay may change. For example, prod must not be able to lower thresholds without going through policy files.
5. **Write the resolved config to a single canonical JSON and SHA-256 it.** The hash, model hash, and code commit are stamped into every response's metadata (as with AWS ModelVersion) and every eval report.
6. **Treat any change to the config hash as a model change for the release gate** (see §10).
7. **Feature flags** are for rollout only, such as enabling a new recognizer in shadow mode. Flags must also be in the hashed config.

**File layout trade-off.** Single file vs split files:
- One file is easy to hash and review atomically, but turns into a large mixed-ownership diff, and evaluation knobs leak into prod.
- **Recommendation: split by ownership and change cadence, then compose into one resolved, hashed artifact.**
```
config/
  schema/                 # pydantic models (code) + exported JSON Schema
  entities.yaml           # taxonomy: ids, HIPAA mapping, direct/quasi, risk tier
  recognizers/
    patterns.yaml         # regex/deny-list recognizers (Presidio-compatible)
    registry.yaml         # per-member registry matching rules (fuzzy, variants)
    exclusions.yaml       # safe/allow lists, exclusion rules with matching type
  models.yaml             # NER model id, weight hash, label_mapping, chunking, device
  reconciliation.yaml     # precedence, overlap merge, score mapping per source
  policy/
    thresholds.yaml       # per entity x modality operating points (+ provenance: which eval run set them)
    transforms.yaml       # per entity action + token scope + TTL
    failure.yaml          # fail-closed behavior per failure class
  environments/
    base.yaml  dev.yaml  test.yaml  prod.yaml   # restricted overlays
  evaluation/             # NOT loaded by the service
    datasets.yaml         # gold set ids + hashes + strata definitions
    gates.yaml            # release gate thresholds (see §10)
    suites.yaml           # checklist suites, perf scenarios
```
Keep `evaluation/` out of the runtime image, so the service cannot read gate thresholds and the eval cannot silently share mutable state. Every threshold in `policy/thresholds.yaml` carries a `provenance` field holding the run ID and dataset hash that justified it.

---

## 10. Production-readiness gates and change management

**Analogues.**
- *SR 11-7* (Fed/OCC 2011): effective challenge, independent validation, ongoing monitoring, outcomes analysis, model inventory. **It was rescinded on 2026-04-17** and replaced by risk-based interagency MRM guidance that explicitly excludes GenAI and agentic models [S, law-firm summaries; the federalreserve.gov page returned 404]. Cite it as historical/analogous only.
- *CHAI*: re-issue the card on use, risk, metric, or structural change [V].
- *GMLP P10* [S].
- *HITRUST*: annual partial and 3-year full review [S].
- *MEASURE 1.3*: independent assessors [V].

**Proposed gate structure.** Numbers are examples to be set by the risk owner; no standard prescribes them.
| Gate | Metric | Example criterion |
|---|---|---|
| G1 Direct identifiers (name, SSN, MRN/member ID, phone, email, address, full DOB) | binary char-level recall per category, per modality | **95% CI lower bound** ≥ 0.99 for typed notes and ≥ 0.97 for ASR. Support ≥ 300 mentions or report as "insufficient evidence" |
| G2 Quasi identifiers (dates, ages >89, geography, facility, profession) | recall per category | lower bound ≥ 0.95 |
| G3 Document-level | fully-sanitized rate (zero direct-ID leak) | ≥ 0.99, with CI |
| G4 Regression | newly missed gold spans vs previous release | 0 new misses in G1 categories (or each one signed off individually) |
| G5 Behavioral | CheckList MFT/INV/DIR pass rate | 100% on MFT for Safe Harbor formats. INV failures triaged |
| G6 Utility | precision / clinical-term over-masking | ≤ X% clinical terms masked. Downstream task delta within tolerance |
| G7 Fail-closed / security | fault-injection suite, log-PHI grep, vault tests | 100% pass |
| G8 Performance | p95/p99 latency, memory, soak | within SLO |
| G9 Monitoring readiness | detection-rate baselines exported, canaries configured | present |
| Overall | ML-Test-Score-style **minimum across gates** | any fail = no-go. Sign-off by an independent reviewer plus the privacy officer |

**Re-validation triggers.**
- Any change in the config hash.
- A model weight or tokenizer change.
- A Presidio or spaCy/transformers version bump.
- A new ASR engine or ASR version.
- A new document type or line of business.
- Drift alarms on detection rate.
- A confirmed production leak.
- Otherwise, at least annually (CHAI, HITRUST).
- A minor change can run the full automated gates. A structural change (new model, new modality) also needs fresh adjudicated gold data from the new condition.

**Where practice is unsettled** (flag these to stakeholders):
- No regulator- or industry-agreed numeric recall threshold for PHI detection.
- No standard calibration method for de-id confidence.
- No published vendor practice for production leak monitoring.
- Whether a PHI-minimized payload to a BAA-covered LLM needs Expert Determination at all. It is generally not "de-identified", so HIPAA BAA and minimum-necessary obligations still apply. This is a legal question, not an engineering one.
- Consistent surrogation scope trades utility against privacy, as both Azure and Osborne 2025 note.

---

## Sources

**Standards, regulation, guidance**
- NIST. *AI Risk Management Framework 1.0* (NIST AI 100-1), 2023; AI RMF Playbook, MEASURE. https://airc.nist.gov/airmf-resources/playbook/measure/ [V]; https://www.nist.gov/itl/ai-risk-management-framework [V]
- NIST. *AI 600-1 Generative AI Profile*, Jul 2024. https://doi.org/10.6028/NIST.AI.600-1 [S]
- IMDRF AIML WG. *Good Machine Learning Practice for Medical Device Development: Guiding Principles* (N88 Final), Jan 2025; FDA/Health Canada/MHRA 2021 original. https://www.fda.gov/medical-devices/artificial-intelligence-enabled-medical-devices/good-machine-learning-practice-medical-device-development-guiding-principles [V date/agencies]; https://imdrf.org/sites/default/files/2025-02/IMDRF_AIML%20WG_GMLP_N88%20Final.pdf [S, fetch timed out]
- ISO/IEC 42001:2023 AI management systems [S]; ISO/IEC 25059:2023 Quality model for AI systems, https://www.iso.org/standard/80655.html [S]
- CHAI. *Assurance Standards Guide* (draft), Jun 2024 [S]; *The CHAI Applied Model Card*, Draft v0.1, Nov 2024. https://mc.chai.org/v0.1/documentation.pdf [V]
- ASTP/ONC. HTI-1 DSI fact sheet (2023) [S]; HTI-5 Proposed Rule (2025–26). https://healthit.gov/resources/health-data-technology-and-interoperability-astp-onc-deregulatory-actions-to-unleash-prosperity-hti-5-proposed-rule/ [S]
- 45 CFR 164.514 (de-identification; re-identification codes). https://www.law.cornell.edu/cfr/text/45/164.514 [V]
- HHS OCR. *Guidance Regarding Methods for De-identification of PHI*, Nov 2012. https://www.hhs.gov/hipaa/for-professionals/special-topics/de-identification/index.html [S, 403 on fetch]
- 45 CFR 164.312 (technical safeguards). https://www.law.cornell.edu/cfr/text/45/164.312 [V]; HIPAA Security Rule NPRM (Jan 6, 2025) status [S, secondary: paubox/physicianspractice 2026]
- NIST SP 800-188, *De-Identifying Government Datasets: Techniques and Governance*, Sep 2023. https://csrc.nist.gov/pubs/sp/800/188/final [V full PDF]
- NIST IR 8053, *De-Identification of Personal Information* (Garfinkel), 2015 [S]
- ISO/IEC 20889:2018 https://www.iso.org/standard/69373.html [S]; ISO 25237:2017 Health informatics: Pseudonymization (revision ISO/DIS 25237: https://www.iso.org/standard/86409.html) [S]
- HITRUST De-Identification Framework, 2015 [S, secondary summaries only]
- UK ICO. *Anonymisation and pseudonymisation guidance*, 2025. https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/data-sharing/anonymisation/ [S]
- NIST SP 800-38G Rev.1 2nd Public Draft (FF1), Feb 2025. https://csrc.nist.gov/pubs/sp/800/38/g/r1/2pd [V]; NIST SP 800-57 Pt.1 Rev.5 (key management) [S]
- NIST SP 800-53 Rev.5: SC-24 [V], SI-11 [V], SI-17 [V] via https://csf.tools/reference/nist-sp-800-53/r5/ ; AC/AU/SC-8/12/13/28, SI-7/12, CM-3/5, SA-11 [S]
- Federal Reserve/OCC SR 11-7 (2011) [S]; rescission by interagency MRM guidance, 2026-04-17, per Orrick summary https://www.orrick.com/en/Insights/2026/04/Agencies-Overhaul-Model-Risk-Management-Guidance-for-Banks-Heres-What-Changed [S]

**Vendor documentation**
- Google Sensitive Data Protection: hotword/likelihood rules https://docs.cloud.google.com/sensitive-data-protection/docs/creating-custom-infotypes-likelihood [V]; exclusion rules https://docs.cloud.google.com/sensitive-data-protection/docs/creating-custom-infotypes-rules [V]; InfoType `version` https://docs.cloud.google.com/sensitive-data-protection/docs/reference/rest/v2/InfoType [V]; templates https://docs.cloud.google.com/sensitive-data-protection/docs/concepts-templates [V]; Healthcare API de-id https://docs.cloud.google.com/healthcare-api/docs/how-tos/deidentify [V]
- AWS Comprehend Medical DetectPHI guide https://docs.aws.amazon.com/comprehend-medical/latest/dev/textanalysis-phi.html [V]; API https://docs.aws.amazon.com/comprehend-medical/latest/api/API_DetectPHI.html [V]
- AWS Transcribe PII redaction https://docs.aws.amazon.com/transcribe/latest/dg/pii-redaction.html [V]; Call Analytics output https://docs.aws.amazon.com/transcribe/latest/dg/tca-output-batch.html [S]
- Microsoft Azure Health Data Services de-identification overview https://learn.microsoft.com/en-us/azure/healthcare-apis/deidentification/overview [V]; Transparency Note https://learn.microsoft.com/en-us/azure/healthcare-apis/deidentification/transparency-note [V]
- Microsoft presidio-research (span_evaluation.md, token_evaluation.md, evaluation.md, ADR-003) https://github.com/data-privacy-stack/presidio-research [V]
- John Snow Labs DeIdentification API https://nlp.johnsnowlabs.com/licensed/api/python/reference/autosummary/sparknlp_jsl/annotator/deid/deIdentification [S]
- Philter-UCSF https://github.com/BCHSI/philter-ucsf [V]
- Private AI customizing detection https://docs.private-ai.com/configuration-and-operations/entity-detection-and-redaction/customizing-detection [S]
- Tonic Textual entity type handling https://docs.tonic.ai/textual/tonic-textual-api/textual-python-sdk/datasets-redaction/api-redaction-entity-type-handling [S]
- Hydra defaults list https://hydra.cc/docs/advanced/defaults_list/ [V]

**Methodology papers**
- Stubbs A, Kotfila C, Uzuner Ö. Automated systems for the de-identification of longitudinal clinical narratives: Overview of 2014 i2b2/UTHealth shared task Track 1. JBI 58 (2015). https://pmc.ncbi.nlm.nih.gov/articles/PMC4989908 [V]
- Stubbs A, Uzuner Ö. Annotating longitudinal clinical narratives for de-identification: the 2014 i2b2/UTHealth corpus. JBI 58 (2015). https://pmc.ncbi.nlm.nih.gov/articles/PMC4978170 [V]
- Ribeiro MT, Wu T, Guestrin C, Singh S. Beyond Accuracy: Behavioral Testing of NLP Models with CheckList. ACL 2020. https://aclanthology.org/2020.acl-main.442/ [V]
- Breck E, Cai S, Nielsen E, Salib M, Sculley D. The ML Test Score. IEEE BigData 2017. https://research.google/pubs/the-ml-test-score-a-rubric-for-ml-production-readiness-and-technical-debt-reduction/ [V abstract]
- Sculley D et al. Hidden Technical Debt in Machine Learning Systems. NeurIPS 2015 [S]
- Mitchell M et al. Model Cards for Model Reporting. FAT* 2019 [S]; Gebru T et al. Datasheets for Datasets. CACM 2021 [S]
- Hanley JA, Lippman-Hand A. If nothing goes wrong, is everything all right? JAMA 1983;249:1743–5 [S]
- Dror R, Baumer G, Shlomov S, Reichart R. The Hitchhiker's Guide to Testing Statistical Significance in NLP. ACL 2018. https://aclanthology.org/P18-1128 [S]
- Beyer B et al. *Site Reliability Engineering* (Google), ch. "Service Level Objectives", 2016 [S]
