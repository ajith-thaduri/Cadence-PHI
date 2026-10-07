# Cadence PHI Shield: production-grade detection, de-identification and evaluation harness

**Status:** plan for review. **No code has been changed.**

**Date:** 2026-10-06. This document replaces all earlier plan drafts.

**Scope**

| Topic | Decision |
|---|---|
| Language | English only: clinical notes, care-management conversations, ASR transcripts |
| Models | Local inference only. OBI RoBERTa i2b2 is the single production model candidate. |
| Orchestration | Microsoft Presidio. Business policy stays in our own code. |
| Excluded | Spanish and code-switched text, any second production model, LLM-based detection, cloud de-identification services |

**Evidence markers used throughout.** `[V]` means the source page was read. `[S]` means only a
search snippet or a secondary summary was seen. Full references are in §19.

---

## Contents

1. [Executive summary](#1-executive-summary)
2. [Industry research findings](#2-industry-research-findings)
3. [Recommended production-grade architecture](#3-recommended-production-grade-architecture)
4. [Current architecture assessment](#4-current-architecture-assessment)
5. [Production-gap analysis](#5-production-gap-analysis)
6. [Configuration architecture](#6-configuration-architecture)
7. [PHI entity strategy](#7-phi-entity-strategy)
8. [Recognizer and model strategy](#8-recognizer-and-model-strategy)
9. [Evaluation and testing strategy (TEVV)](#9-evaluation-and-testing-strategy-tevv)
10. [Prompt suite and test-case design](#10-prompt-suite-and-test-case-design)
11. [Failure-analysis strategy](#11-failure-analysis-strategy)
12. [Redaction and residual-PHI evaluation](#12-redaction-and-residual-phi-evaluation)
13. [Regression strategy](#13-regression-strategy)
14. [Privacy and security controls](#14-privacy-and-security-controls)
15. [Production-readiness gates](#15-production-readiness-gates)
16. [Implementation phases](#16-implementation-phases)
17. [Files and modules to create or modify](#17-files-and-modules-to-create-or-modify)
18. [Testing and validation plan](#18-testing-and-validation-plan)
19. [Decisions required](#19-decisions-required)
20. [References](#20-references)

---

## 1. Executive summary

**Current state.** The detection engine is already production-*shaped*:
- typed contracts;
- coordinates always in the original text;
- a full evidence chain for every decision;
- Presidio orchestration with its silent filtering bypassed;
- detection and redaction kept separate;
- calibration that only uses development data;
- a tool for manual evaluation.

**The gap.** The assurance layer around the engine is still research-grade:
- the repository is not under version control;
- failure handling is undefined and untested;
- there are no release gates and no confidence intervals;
- the gold data is fabricated and single-annotated;
- the token vault is in-memory and unencrypted;
- entity behaviour is partly hard-coded in Python;
- there is no performance testing and no monitoring.

**What industry practice requires.** NIST AI RMF, NIST SP 800-188 and 800-53, 45 CFR 164.514,
the vendor transparency documents and the de-identification literature agree on the following:
- **Recall is the release metric.** Gate on its lower confidence bound for each entity and
  modality, not on F1.
- **Release evidence must come from real data in deployment conditions.**
- **The system must fail closed,** and this must be proven by fault injection.
- **Every output is stamped** with the config hash, model hash and code commit.
- **Configuration separates policy from mechanism.** It is strictly validated, composed from
  layers and hashed.
- **The re-identification map is randomly derived and strongly protected.**
- **Behavioural (CheckList) suites and a regression baseline** run on every change.

**Plan in brief.** Six phases:

| Phase | Content |
|---|---|
| **P0** Foundations | version control, fail-closed behaviour, PHI-free logging |
| **P1** Configuration and entity catalog | entity behaviour moves out of code |
| **P2** Evaluation core | three-view metrics with confidence intervals, contamination control, redaction checks, stage attribution |
| **P3** Test suites | behavioural tests, prompt suite, performance tests |
| **P4** Gates and regression | gate runner, regression baselines, system card |
| **P5** Production readiness | vault, outbound guard, monitoring, first evaluation on real governed data |

**Critical gaps** (4 of 26):
- G-01 no version control
- G-02 fail-closed behaviour undefined
- G-03 no release evidence or gates
- G-04 the vault is not production-safe

---

## 2. Industry research findings

### 2.1 Assurance frameworks (TEVV)

**NIST AI RMF 1.0, MEASURE function** [V]. Testing, evaluation, verification and validation
(TEVV) must be documented and repeatable:
- MEASURE 2.1: test sets, metrics and tools are documented.
- MEASURE 2.3: evaluation happens under conditions representative of deployment.
- MEASURE 2.6: the system fails safely.
- MEASURE 1.3: there are independent assessors.
- Privacy risk is examined explicitly.

**Good Machine Learning Practice** (FDA, Health Canada, MHRA; IMDRF N88 2025) [S]:
- test data stays independent of training data;
- reference standards are fit for purpose;
- the system is evaluated in clinically relevant conditions;
- deployed performance is monitored.

**CHAI Applied Model Card** (draft v0.1, 2024) [V]:
- a release-level card covering safety/reliability, fairness and usefulness;
- the card is re-issued on any change of use, risk, metric or structure.

**Regulatory status notes:**
- **ONC HTI-1 model-card requirements** are proposed for removal under HTI-5 [S], so they should not
  be cited as binding.
- **SR 11-7 model risk management** is useful as an analogy only (independent validation, ongoing
  monitoring). It was reportedly rescinded in 2026 [S].

### 2.2 De-identification standards

- **45 CFR 164.514(b) Safe Harbor** [V]:
  - removes the 18 identifier types, including all date elements except the year, ages over 89,
    geography below state, and the first three ZIP digits under the population rule;
  - Expert Determination requires a "very small" re-identification risk, with **no numeric
    threshold**.
- **45 CFR 164.514(c)** [V]: a re-identification code must **not be derived from or related to
  information about the individual**, and the mechanism must not be disclosed.
- **NIST SP 800-188 (2023)** [V]:
  - de-identification software must be verified for correctness, **containment** (no leaks "in
    expected or unexpected ways"), usability, efficiency, scalability, **repeatability** and
    transparency;
  - lookup-table pseudonyms "must be randomly assigned", and the table must be highly protected;
  - tests should use conspicuously non-natural fakes such as "FIRSTNAME1".
- **HITRUST** de-identification framework [S]: the organisation must define its own thresholds
  and governance.
- **NIST SP 800-38G Rev.1** (FF1 format-preserving encryption, 2nd draft, 2025) [V]: FF3 is
  removed and the minimum domain is 10⁶. Ages and day-of-month fields are therefore not FF1
  domains.

### 2.3 How production de-identification services are built and documented

- **Google Sensitive Data Protection / Healthcare API** [V]:
  - *inspection* config (infoTypes, custom detectors, exclusion and hotword rules, likelihood) is
    separated from *de-identification* config (transformations);
  - reusable templates;
  - a version field on infoTypes, though templates themselves are not versioned. That is a gap we
    should not copy.
- **AWS Comprehend Medical DetectPHI** [V]: returns `ModelVersion` and a confidence score on every
  response.
- **AWS Transcribe PII redaction** [V]: "does not meet the requirements for de-identification
  under HIPAA".
- **Azure Health Data Services de-identification** [V]:
  - its **primary release criterion is recall**, with a goal above 95% (entity-level, fuzzy
    matching);
  - it warns that "small datasets, synthetic data, and tests that don't reflect your end-to-end
    scenario are unlikely to sufficiently represent your production performance";
  - it prefers surrogates, but warns that **consistent surrogation over large batches degrades
    privacy**;
  - it is not recommended for real-time transcription.
- **presidio-research** [V]: span (IoU) and token evaluators, F2, and a hierarchy-aware entity
  mapper. Suitable as an independent cross-check of our scorer.
- **Presidio itself** [V, source]:
  - `AnalyzerEngine.analyze()` drops results below threshold, then drops results contained in a
    same-type result, so evidence is lost;
  - `AnalyzerEngineProvider` **silently falls back to defaults** when the config file is missing;
  - `log_decision_process=True` writes raw entity text to a logger.

### 2.4 De-identification literature

**Architecture size:**
- One model plus rules plus dictionaries is the proven size: the i2b2 2014 winner (Yang &
  Garibaldi 2015) [S] and Dehghan 2015 [V].
- **Ensembles add 1.1–1.5 F1 and no recall** (Kim, Heider & Meystre 2020) [V].
- Rules plus dictionaries can reach 99%+ recall at roughly 78% precision (Philter, Norgeot 2020)
  [V].
- A hybrid beats the same NER model alone by about 10% (Kocaman 2023) [V].

**Patient-specific dictionaries** work: CRIS reached P 98.8 / R 97.6 (Fernandes 2013) [V].
Propagating confident detections to every mention also helps (Dehghan 2015).

**Generalisation:**
- Expect **10–20 points lower at a new site**: Yang 2019 fell from 0.955 to 0.857 F1 [V].
- Local fine-tuning plateaus at about 700 notes (Yang 2020) [V].
- Synthetic PHI overstates performance (Yeniterzi 2010) [S].

**Speech (ASR):** word error rate on PHI words was 41.8%, and recall of an i2b2-trained model on
conversational transcripts was 0.37. Of the false negatives, 45% were caused by ASR and 50% by NER
(Cohn 2019) [V].

**Residual risk:**
- Every method leaves residual identifiers.
- Realistic surrogates hid about 90% of leaked identifiers (Carrell 2013) [V], and motivated
  attackers found only 26.8% of leaked PII (Carrell 2020) [V].
- Consistent replacement turns tiny token miss rates into large document-level leakage
  (Osborne 2025) [V].
- Presidio uncustomised on clinical notes scored only P 0.51 / R 0.74 (Alrazihi 2025) [S].

### 2.5 Evaluation methodology

**i2b2/n2c2 2014** [V]:
- strict and relaxed entity matching;
- binary token-level PHI recall;
- HIPAA-only and full label sets reported separately;
- double annotation plus adjudication, with inter-annotator agreement of token F1 0.93 and entity
  F1 0.895.

**Small-sample statistics:**
- **Rule of three** (Hanley 1983) [S]: zero misses in n gives a 95% upper bound of about 3/n on
  the miss rate. About 300 mentions per category are needed to bound the miss rate below 1%.
- Use a **cluster bootstrap over documents** (tokens within a document are correlated).
- Use Wilson or Clopper-Pearson intervals for proportions.
- Use **paired approximate randomisation** to compare system versions (Dror 2018) [S].

**Behavioural testing with CheckList** (Ribeiro 2020) [V]:
- *minimum functionality* tests (MFT);
- *invariance* tests (INV);
- *directional* tests (DIR).

They find failures that held-out accuracy misses.

**Documentation:** model cards (Mitchell 2019) and datasheets (Gebru 2021) [S].

### 2.6 MLOps, reliability and security

- **ML Test Score** (Breck 2017) [V abstract]:
  - check the model before serving (startup self-test on canary data);
  - monitor the deployed model;
  - make readiness the *minimum* across test areas.
- **Fail-safe controls in NIST SP 800-53** [V]:
  - SC-24: fail in a known state;
  - SI-17: fail-safe procedures;
  - SI-11: error messages carry no sensitive information.

  No vendor documents "fail-closed redaction" explicitly, so these generic controls are the basis.
- **Latency targets are set as p50/p95/p99, not means.** Load, soak and cold-start testing are
  standard (Google SRE) [S].
- **Document size limits:** Azure accepts 50 KB per request and AWS 20 KB per call [V]. Inputs
  beyond the limit must be rejected or split, **never silently truncated**.
- **HIPAA technical safeguards** (45 CFR 164.312) [V]: access control, audit controls, integrity,
  transmission security.
- **Logs** may hold only *keyed* hashes. Plain hashes of a DOB or SSN can be brute-forced.
- **Production leak monitoring** is emerging practice, not standardised. It covers detection-rate
  control charts, sentinel canary records sent through the real path, an outbound payload scan,
  and sampled human review.

### 2.7 Unsettled in industry, to be raised with stakeholders

- What numeric recall thresholds to adopt.
- How to calibrate de-identification confidence scores.
- How to monitor leaks in production.
- The right scope for consistent tokens.
- Whether a PHI-minimised payload sent to a BAA-covered LLM needs Expert Determination. It is
  generally *not* legally "de-identified". This is a legal question.

---

## 3. Recommended production-grade architecture

```
            ┌───────────────────────────── RUNTIME (shipped) ──────────────────────────────────────┐
config/  ─► │ ConfigLoader: compose → env overlay → calibrated overlay → strict schema → resolved   │
(layered,   │   JSON + SHA-256 → startup self-test on sentinel text  (refuse to start on failure)    │
 hashed)    │                                                                                       │
            │ DetectionRequest ─► Guard (language, size limit, deadline budget)                     │
            │   ─► TextViews (normalizers; exact offset maps; approximate views = evidence only)    │
            │   ─► Presidio EvidenceAnalyzer (all evidence kept; no built-in filtering)             │
            │        declarative pattern recognizers │ conversational rules │ scoped registry       │
            │        ModelRecognizerAdapter (production | shadow) │ Presidio predefined (optional)  │
            │   ─► Policy: entity catalog, thresholds (entity × model × modality), exclusions       │
            │   ─► Reconciliation: union, word expansion, precedence, subtype refinement           │
            │   ─► DetectionResponse (stamped: config/model/code/catalog versions)                 │
            │   ─► Redaction (operators per entity) ─► TokenVault (encrypted, scoped, audited)      │
            │   ─► Outbound guard (independent rules+registry re-scan; block on direct-ID hit)      │
            │ Fail-closed handler: any error/timeout/oversize/config/vault failure → BLOCK          │
            │   (or configured conservative fallback) — never pass-through                          │
            │ Telemetry: PHI-free structured logs, metrics, detection-rate counters                 │
            └───────────────────────────────────────────────────────────────────────────────────────┘
            ┌──────────────────────── EVALUATION (separate package, never shipped) ─────────────────┐
            │ dataset manifests (roles, hashes, PHI class) · contamination checks · evaluation ledger │
            │ tiers T0–T8 (unit … release … monitoring) · prompt suite · CheckList suites           │
            │ scorer: leak view · strict/relaxed entity · document level · CIs · strata             │
            │ stage attribution · source-group attribution · ablation · redaction validation        │
            │ gate runner (gates.yaml → go/no-go) · regression baselines · system card, datasheets  │
            └───────────────────────────────────────────────────────────────────────────────────────┘
```

**Principles**

1. **Policy is separate from mechanism.**
   - Entity behaviour (enablement, risk tier, thresholds, actions) lives in configuration.
   - Recognizers only declare which entities they can emit.
2. **Runtime and evaluation are separate packages.**
   - Gold labels, gate thresholds and datasets never reach the engine.
   - An import rule enforces this.
3. **Fail closed by default,** proven by fault injection.
4. **Every artifact is versioned and stamped.** Every response and report carries the config
   hash, model weight hash, code commit and catalog version.
5. **Gate on recall lower bounds** per risk tier and modality. Also gate on document-level
   sanitisation, regression, behavioural, security and performance results. Any single failure
   means no-go.
6. **Detection and redaction are judged independently.** Residual PHI is measured on the
   *output*.

---

## 4. Current architecture assessment

This is what exists today, verified by read-only inspection.

| Area | Implemented | Assessment |
|---|---|---|
| Contracts | Pydantic `DetectionRequest`, `DetectionResponse`, `DetectionEvidence`, `Detection`, `RedactionRequest`, `RedactionResponse`, `GoldDocument`, `EntityOutcome`, `EvaluationRecord`. Original-text `[start, end)` coordinates. | **Keep.** Extend additively. |
| Engine | `PHIEngine.analyze`, `redact(RedactionRequest)`, `restore`. English-only profile. Process-wide model cache. Config hash in every response. | **Keep.** Add fail-closed handling, stamping and telemetry. |
| Detection | Rule families, six conversational rules, scoped registry (exact / normalised / fuzzy), OBI adapter (pinned revision), Presidio predefined recognizers (off). Presidio filtering bypassed. | **Keep.** Move entity tables and keywords to config. Add declarative patterns. |
| Normalization | Six versioned normalizers. Exact segment maps. Approximate mappings cannot drive redaction. | **Keep.** |
| Policy | Threshold precedence with recorded source. Type precedence, subtype refinement (full-span only), allow-list, REVIEW band. | **Keep.** Add modality dimension and per-entity actions. |
| Redaction | Presidio AnonymizerEngine. REDACT / TOKENIZE / PSEUDONYMIZE / ENCRYPT. Token map not in responses. | Vault **not production-safe** (G-04). |
| Configuration | One YAML file plus a generated calibrated overlay. Presets, request allow-lists, CLI overrides. Generated responsibility matrix. | **Monolithic.** No strict schema, no environment layers. Entity behaviour partly in code (G-06, G-08). |
| Evaluation | Dataset adapter (request plus separate gold, English filter). Residual-PHI scorer. Seven failure outcomes. Dev-only calibration that refuses test data. Ablation ladder. `harness.phi.analyze` (file, directory or JSONL; optional gold; HTML/MD report). | Good base. **No confidence intervals, strict/relaxed scoring, manifests, ledger, contamination checks, redaction validation or full stage tree** (G-09 to G-13). |
| Tests | 132 passing: contract, threshold precedence, evidence chain, offsets, privacy of responses and logs on tested paths, determinism, batch vs single. | No fault-injection, performance, behavioural or regression-baseline tiers. |
| Documentation | Engine contract, Presidio architecture, manual evaluation, rule changelog, research notes. | No annotation guidelines, gates doc, security doc or system card. |
| Data | Fabricated Cadence-realistic dev and test sets (English subset: dev 38+27 documents, test 54+38). Simulated ASR (TTS → phone channel → Whisper). | **Engineering evidence only.** No real governed data (G-03). |

---

## 5. Production-gap analysis

**Priority levels**

| Priority | Meaning |
|---|---|
| **Critical** | Blocks any production claim, or is a safety or audit hole. |
| **High** | Required for a production-grade harness. |
| **Medium** | Maintainability and operations. |
| **Low** | Nice to have. |

| ID | Gap (what is missing or not production-grade) | Current state | Why it matters | Priority |
|---|---|---|---|---|
| G-01 | No version control | Repository is not a git repo. No commit to stamp, no change history. | Auditability, reproducibility and change management are impossible (AI RMF MEASURE 2.1; SP 800-188 repeatability). | **Critical** |
| G-02 | Fail-closed behaviour undefined and untested | Exceptions propagate. No timeouts, no input-size limit, no startup self-test, no fallback policy. | A shield must fail in a known safe state (SC-24, SI-17). Pass-through or silent truncation is the worst failure. | **Critical** |
| G-03 | No release evidence or gates | All data fabricated. Gold single-authored. No inter-annotator agreement, no CIs, no gate definitions. | "Production ready" cannot be decided (MEASURE 2.3; Azure TN). | **Critical** |
| G-04 | Token vault not production-safe | In-memory only. Not encrypted, no access audit. Pseudonym key falls back to random (inconsistent). Consistency scope fixed at document. | 164.514(c); SP 800-188 table protection. Restore must work across processes. | **Critical** (production path) |
| G-05 | No outbound payload guard | Nothing re-scans sanitised text before the LLM call. | Last line of defence; the only end-to-end leak control that needs no labels. | **High** |
| G-06 | Entity behaviour hard-coded | Taxonomy, high-risk list, title / ID-affix / edge-word lists, rule-family→entity and registry-kind→entity tables, ID and DOB context keywords, token formats all live in Python. | Adding, disabling or modifying an entity should not need code. | **High** |
| G-07 | HIPAA Safe Harbor mapping incomplete | No catalog mapping. AGE > 89, license/certificate, vehicle, device, IP and fax have no explicit entity or recognizer. SSN and health-plan IDs are folded into other labels. | Coverage claims must trace to the 18 identifiers. | **High** |
| G-08 | Configuration architecture | Single mixed-ownership file. Custom validation on raw dicts. No environment overlays. No threshold provenance. Evaluation presets ship in runtime config. | Maintainability, review, safe production overrides. Presidio's silent-default risk. | **High** |
| G-09 | Evaluation methodology incomplete | No strict/relaxed entity scoring, no document-level rate with CI, no token-level binary recall, no CIs, no paired tests, no strata by length / document type / subgroup. | Industry-standard reporting; honest small-sample statistics. | **High** |
| G-10 | Gold and test-data management | Hashes recorded, but no manifest of roles, provenance, PHI class or access. No annotation guidelines in the repo. No double annotation or agreement measurement. No gold versioning. Dev/test separation by convention. No contamination checks. No log of test-set access. | Prevents leakage and overfitting to the test set. Required for TEVV. | **High** |
| G-11 | No behavioural (CheckList) suites | Unit tests and fabricated corpora only. | Systematic format, invariance and directional coverage; cheap regression. | **High** |
| G-12 | Redaction not validated independently of detection | Residual computed from detection coverage. | Detection and redaction failures are conflated. | **High** |
| G-13 | Failure attribution incomplete | Seven outcomes. No `normalization_failure` or `redaction_failure`. Coarse source groups. No marginal ablation in reports. | Exact failed stage and component cannot be named. | **High** |
| G-14 | PHI-free logging not enforced | Discipline only. No log filter or allow-listed structured logging. Log-grep tests cover some paths only. | One stray log call leaks PHI. | **High** |
| G-15 | No performance or reliability testing | Per-request timings only. | p50/p95/p99 per stage, throughput, soak, cold start, chunk-boundary behaviour are all unmeasured. | **High** |
| G-16 | No modality-specific policy | Thresholds by model × entity only. | ASR needs its own operating points and gates (Cohn 2019; vendors). | **High** |
| G-17 | Runtime and evaluation mixed in one package | `harness/phi` holds engine and evaluation; the legacy benchmark sits alongside. | Runtime could import gold or evaluation code. Review boundaries. Image size. | **Medium** |
| G-18 | No monitoring hooks | No metrics export, detection-rate counters, canaries or shadow mode. | Regression detection in production without labels. | **Medium** (High before go-live) |
| G-19 | No system card or datasheets | Results files only. | Transparency; re-issue on change (CHAI). | **Medium** |
| G-20 | No model integrity check | Revision pinned. Weight hash neither verified nor stamped. | Supply-chain integrity; reproducibility. | **Medium** |
| G-21 | Cross-device determinism unverified | Same-device determinism tested. CPU vs MPS/GPU not tested. Torch determinism flags not set. | Repeatability. | **Medium** |
| G-22 | Registry matching parameters in code | Fuzzy limits, nicknames, particles, capitalisation rule. | Configurability; reviewable registry policy. | **Medium** |
| G-23 | Dependency pinning and egress not enforced | `requirements.txt` without hashes. Offline mode via an environment variable only. No socket-block test. | Supply chain; a no-egress guarantee. | **Medium** |
| G-24 | Inert Spanish code paths | Out-of-scope branches in shared rules. | Clarity and attack surface. | **Low** |
| G-25 | No independent metric cross-check | Our scorer only. | Verification of metric code (presidio-research). | **Low** |
| G-26 | GEOGRAPHIC_CONTEXT has no recognizer | Covered only incidentally by address and city spans. | Quasi-identifier gap. | **Low** (unless gates require it) |

**Keep as is:**
- the contracts and evidence chain;
- the original-coordinate guarantee;
- the Presidio filter bypass;
- recording which config key supplied each threshold;
- dev-only calibration that refuses test data;
- redaction kept separate from detection;
- `harness.phi.analyze`;
- the responsibility matrix (to be generated from the catalog).

---

## 6. Configuration architecture

### 6.1 Recommendation

**Split configuration by owner and change cadence, then compose it into one resolved, strictly
validated, hashed artifact. Evaluation configuration stays out of the runtime.**

This follows four precedents:
- Google DLP's inspect/de-identify split;
- Hydra-style ordered composition;
- Kustomize-style base-plus-overlay environments;
- Presidio's own pydantic validation, without its silent default fallback.

**Why not one file:**
- every change becomes a mixed-ownership diff (privacy policy vs detector mechanics);
- environment differences can't be restricted;
- evaluation settings leak into production.

**Why not many unconnected files:** behaviour becomes unauditable unless the files compose into
one hashed artifact.

### 6.2 Layout

```
configs/phi/                               # RUNTIME configuration (shipped)
  phi.yaml                                 # composition root: schema version, ordered includes, active environment
  entities.yaml                            # ENTITY CATALOG — owner: privacy/compliance
  recognizers/
    patterns.yaml                          # declarative pattern recognizers (regex, context words, named validators)
    contextual.yaml                        # conversational/ASR recognizers: keywords, guards, scores
    registry.yaml                          # registry kinds → entities; exact/normalized/fuzzy parameters; nicknames
    external.yaml                          # Presidio predefined recognizers + entity_map (off by default)
    exclusions.yaml                        # allow-list / deny-list / exclusion rules (incl. medical-term safe list)
  models.yaml                              # production model(s): key, revision, weight sha256, label mapping, chunking,
                                           #   device, candidate floor, mode: production | shadow | disabled
  normalization.yaml                       # normalizers (versions, mapping quality) + named profiles
  reconciliation.yaml                      # precedence, type refinements, word-expansion rule
  policy/
    thresholds.yaml                        # default / model / entity / model×entity × modality, each with provenance
    redaction.yaml                         # action per entity or risk tier, token format, consistency scope, surrogates
    failure.yaml                           # fail-closed mode per failure class, deadlines, max input size
    profiles.yaml                          # policy profiles (cadence_default, research, …)
  environments/
    base.yaml   dev.yaml   test.yaml   prod.yaml      # restricted overlays (allow-listed keys only)
  generated/                               # written ONLY by tools, never by hand
    thresholds_calibrated.yaml             # from `calibrate` on dev data, with provenance
    entity_responsibility.yaml             # from the matrix generator
configs/eval/                              # EVALUATION configuration (never shipped, never loaded by the engine)
  datasets.yaml                            # manifests: id, path, sha256, role, provenance, PHI class, access class, license
  suites.yaml                              # behavioural / prompt / fault-injection / performance suite definitions
  gates.yaml                               # release gates (risk-owner signed) + insufficient-evidence rules
  metrics.yaml                             # metric definitions, CI method, strata
  presets.yaml                             # ablation presets (moved out of runtime config)
configs/models.yaml, configs/label_mapping/      # existing research benchmark catalog (kept, research only)
```

### 6.3 Example: entity catalog (`entities.yaml`)

```yaml
catalog_version: "2026.10.0"
risk_tiers: {direct: "uniquely identifies a person", quasi: "identifies in combination", contextual: "organisational context"}
entities:
  NAME:
    description: "Name of the member (patient) or other individual referred to as the subject"
    category: person
    hipaa_safe_harbor: [names]
    risk_tier: direct
    enabled: true
    default_action: TOKENIZE
    aliases: [PERSON, PATIENT_NAME]
    subtypes: [MEMBER_NAME, PARTIAL_NAME, SPELLED_NAME]
    core_chars: {strip_titles: [dr, mr, mrs, ms, miss, sr, sra, nurse], strip_edge_words: true}
    outbound_guard: block
  PHONE:
    description: "Telephone or fax number"
    category: contact
    hipaa_safe_harbor: [telephone, fax]
    risk_tier: direct
    enabled: true
    default_action: TOKENIZE
    subtypes: [PHONE, FAX, PARTIAL_PHONE]
    outbound_guard: block
  DATE:
    description: "Any date element (except year) related to an individual"
    category: date
    hipaa_safe_harbor: [dates]
    risk_tier: quasi
    enabled: true
    default_action: TOKENIZE          # alternative: GENERALIZE (keep year)
    subtypes: [ADMISSION_DATE, DISCHARGE_DATE, APPOINTMENT_DATE, PROCEDURE_DATE, SERVICE_DATE, RELATIVE_DATE]
    core_chars: {strip_edge_words: true}
  AGE_OVER_89:
    description: "Age above 89 and its elements (Safe Harbor)"
    category: person
    hipaa_safe_harbor: [age_over_89]
    risk_tier: quasi
    enabled: false                    # opt-in until its recognizer is developed and evaluated on dev data
```

Every other file refers to entities by catalog id, and the schema rejects unknown ids. The engine
reads risk tier, actions, core-character rules and outbound-guard behaviour from the catalog. These
replace the Python constants `HIGH_RISK`, `DEFAULT_TITLES`, `ID_AFFIXES` and `EDGE_WORDS`.

### 6.4 Example: declarative pattern recognizer (`recognizers/patterns.yaml`)

```yaml
patterns:
  rule_npi:
    version: "1"
    entity: NPI
    patterns:
      - {name: npi_keyword, regex: '(?i)\bNPI\b\W{0,3}(\d{10})\b', group: 1, score: 1.0}
    validators: [npi_luhn]            # named validator → score_type checksum_valid
    context: [npi, provider]
    developed_on: [synthetic_dev]
  rule_ssn:
    version: "1"
    entity: SSN
    patterns:
      - {name: ssn_dashed, regex: '\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b', score: 0.9}
    context: [ssn, social security]
    enabled: false
```

**Adding a deterministic entity needs no code:**
1. add a catalog entry;
2. add a pattern entry;
3. optionally add policy overrides.

A test proves this. Complex families (spoken-number parsing, conversational rules) stay in code,
but take every keyword, guard and score from `contextual.yaml`.

### 6.5 Example: policy files

```yaml
# policy/thresholds.yaml
default: 0.50
deterministic_default: 0.0
applies_to_score_types: [model_confidence]
model: {obi_roberta: 0.50}
entity: {}
model_entity:
  obi_roberta: {NAME: 0.85, DATE: 0.85, PHONE: 0.90}
modality:                                   # NEW: per-modality operating points
  asr: {model_entity: {obi_roberta: {NAME: 0.60}}}
provenance:                                 # every non-default value must cite its evidence
  model_entity.obi_roberta.NAME: {run_id: calib-2026-10-06, datasets: [cadence_dev_text, cadence_dev_asr],
                                  dataset_sha256: "...", rule: "highest threshold within 1% residual of minimum"}

# policy/failure.yaml
on_model_error: block                       # block | conservative_fallback
on_timeout: block
on_oversize_input: reject                   # reject | split  (never truncate)
on_vault_unavailable: block
on_config_invalid: refuse_start
deadline_ms: {total: 2000, model: 1500}
max_input_chars: 50000
startup_self_test: required

# policy/redaction.yaml
default_operator: TOKENIZE
token_format: "[{ENTITY}_{N}]"
consistency_scope: conversation             # document | conversation | member  (never global)
per_entity: {DATE: GENERALIZE_YEAR}        # optional
vault: {backend: encrypted_file, key_ref: env:PHI_VAULT_KEY, ttl_days: 30, audit: true}
```

### 6.6 Resolution order and hashing

1. Composition root and its includes, in a fixed order.
2. Environment overlay. Only allow-listed keys may change. **Prod cannot lower thresholds,
   disable a direct-identifier entity or turn off the outbound guard.**
3. Generated calibrated overlay, validated against `provenance`.
4. CLI overrides (evaluation only).
5. Request allow-lists, which can only **narrow** what is enabled.

Then strict Pydantic validation:
- `extra="forbid"`;
- value ranges checked;
- references checked against the catalog;
- the loader **refuses to start** if a file is missing.

The validated config is serialised to canonical JSON and hashed with SHA-256. Every response and
report is stamped with:
- `config_sha256`
- `catalog_version`
- `code_commit`
- `model_weight_sha256`
- `engine_version`

`python -m harness.phi.config validate --env prod` prints the resolved hash.

### 6.7 Threshold precedence (current order kept, plus modality)

For `model_confidence` evidence, the most specific setting wins:

```
model×entity×modality > model×entity > entity×modality > entity > model > recognizer > default
```

Deterministic and registry evidence keeps its own semantics: `recognizer > deterministic_default`.
Entity and model ML thresholds never suppress deterministic or registry evidence.

### 6.8 Migration

- `configs/phi_harness.yaml` stays readable.
- `python -m harness.phi.config migrate` splits it into the new layout.
- An equivalence test asserts that old and new layouts produce identical detections on the
  regression corpus.

---

## 7. PHI entity strategy

### 7.1 Entities and detection responsibility

| Entity (canonical) | HIPAA Safe Harbor | Risk tier | Evaluation subtypes | Detection sources |
|---|---|---|---|---|
| NAME | names | direct | MEMBER_NAME, PARTIAL_NAME, SPELLED_NAME | registry (exact, fuzzy), OBI, titled-name rule, spelled-name rule (spoken) |
| RELATIVE_NAME | names | direct | SPOUSE_NAME, PARENT_NAME, CHILD_NAME, CAREGIVER_NAME | OBI (primary), registry contacts |
| CARE_TEAM_MEMBER | names | contextual | STAFF_NAME | registry staff directory, OBI (as NAME or PROVIDER; scored as compatible) |
| PROVIDER_NAME | names | contextual | PROVIDER | registry provider directory, titled-name rule, OBI |
| FACILITY_NAME | geographic / name | quasi | HOSPITAL, CLINIC, PHARMACY | OBI, registry facility directory |
| ADDRESS | geographic < state | direct | STREET_ADDRESS, PO_BOX, UNIT | address rule, spoken-address rule + normaliser, registry record, OBI |
| CITY | geographic < state | quasi | – | address rule, OBI, registry record |
| STATE | (allowed under Safe Harbor) | contextual | – | address rule |
| ZIP | geographic | quasi | ZIP_CODE, SPOKEN_ZIP | ZIP rule, spoken digits + spoken-ZIP rule, registry record |
| GEOGRAPHIC_CONTEXT | geographic < state | quasi | LANDMARK, NEIGHBORHOOD | **none** (gap G-26) |
| DOB | dates | direct | SPOKEN_DOB | date rule with DOB context, spoken-DOB rule, registry DOB (incl. spoken forms), OBI |
| DATE | dates | quasi | ADMISSION / DISCHARGE / APPOINTMENT / PROCEDURE / SERVICE / RELATIVE | date rule, ordinal-date rule + spoken-dates normaliser, OBI |
| AGE_OVER_89 *(new, opt-in)* | ages > 89 | quasi | – | new rule (ages ≥ 90 with age context) |
| PHONE | telephone, fax | direct | FAX, PARTIAL_PHONE, SPOKEN_PHONE | phone rule, spoken-number rule, spoken-ID / partial-phone rules, registry, OBI |
| EMAIL | email | direct | SPOKEN_EMAIL | email rule, spoken-email normaliser, spelled-name (EMAIL) rule, registry record |
| URL | URLs | direct | – | email/URL rule |
| IP_ADDRESS *(new)* | IP address | direct | – | pattern (IPv4/IPv6); optional Presidio IpRecognizer |
| SSN *(new)* | SSN | direct | – | pattern + context (currently folded into OTHER_UNIQUE_ID); optional Presidio UsSsnRecognizer |
| MRN | medical record numbers | direct | SPOKEN_MRN | keyword-ID rule, spoken-ID rule, registry identifiers |
| MEMBER_ID | health plan beneficiary numbers | direct | HEALTH_PLAN_ID, POLICY_ID, MBI, SPOKEN_MEMBER_ID | keyword-ID rule, spoken-ID rule, registry; optional Presidio UsMbiRecognizer |
| ACCOUNT_ID | account numbers | direct | – | keyword-ID rule, spoken-ID rule |
| LICENSE_CERT_ID *(new)* | certificate / license numbers | direct | DRIVER_LICENSE, MEDICAL_LICENSE | pattern + context; optional Presidio license recognizers |
| VEHICLE_ID *(new)* | vehicle identifiers | direct | VIN, PLATE | pattern + context |
| DEVICE_ID *(new)* | device identifiers / serials | direct | SERIAL | keyword-ID rule (serial / device) |
| NPI | (provider identifier) | contextual | – | keyword-ID rule (Luhn → `checksum_valid`), registry; optional Presidio UsNpiRecognizer |
| OTHER_UNIQUE_ID | any other unique number | direct | CLAIM, CASE, AUTH, ORDER, GATE_CODE | keyword-ID rule, spoken-ID rule, OBI |
| EMPLOYER | (context) | quasi | – | OBI, registry employer |
| ORGANIZATION | (context) | contextual | SCHOOL, AGENCY | OBI |
| LANGUAGE | (context) | contextual | – | preferred-language rule |

Biometric identifiers and full-face photos are out of scope for text.

### 7.2 Entity policy

- **Per entity:**
  - action (TOKENIZE / REDACT / SURROGATE / GENERALIZE_YEAR for dates);
  - per-modality thresholds;
  - consistency scope;
  - outbound-guard behaviour (`block` for every direct identifier).
- **New entities start `enabled: false`.** They are enabled only after their recognizer is developed
  on dev data and passes the behavioural suite. Each change is logged in the rule changelog.
- **Evaluation subtypes** carry the fine-grained requirements (spouse, admission date, hospital …)
  without growing the runtime taxonomy. Reports cover every canonical entity *and* every subtype.
- **The generated responsibility matrix flags:**
  - entities with no enabled recognizer;
  - entities with a single point of failure;
  - mismatches between *expected* and *actual* source in evaluation.

---

## 8. Recognizer and model strategy

- **Deterministic rules.**
  - Simple families move to declarative `patterns.yaml`.
  - Validators are chosen by name (`luhn`, `npi_luhn`, `date_valid`).
  - Complex parsers (spoken numbers) stay in code, with their parameters in configuration.
- **Conversational / ASR rules** stay in code.
  - Keywords, guards and scores move to `contextual.yaml`.
  - Each rule is developed on dev data only and logged in `docs/rule_changelog.md`.
- **Registry.**
  - Matching parameters move to `registry.yaml`.
  - Access stays scoped to the subject plus versioned directories.
  - Each lookup is audited by subject id and directory version, never content.
  - Each match records `match_type`, `registry_type` and `registry_version`.
- **One production model: OBI RoBERTa**, behind the generic `ModelRecognizerAdapter`.
  - The weight SHA-256 is verified at load; a mismatch fails closed.
  - Revision and hash are stamped on every response.
  - `mode: production | shadow | disabled`. Shadow evidence is recorded and evaluated but never
    changes a decision, so a future model can be trialled without risk.
  - Stanford V2 and MedNotes GLiNER remain experimental adapters.
- **Recognizer contract tests** that every recognizer must pass:
  - never mutates text;
  - spans stay within bounds;
  - emits declared entities only;
  - deterministic;
  - within its time budget on the long-document fixture.
- **Model-change protocol:**
  1. shadow run;
  2. paired approximate-randomisation comparison on the release set;
  3. all gates;
  4. system card re-issued.

  A second production model requires the pre-registered materiality protocol. The literature shows
  ensembles add no recall (§2.4).

---

## 9. Evaluation and testing strategy (TEVV)

### 9.1 Test tiers

| Tier | Data | Purpose | When | Gate |
|---|---|---|---|---|
| T0 unit / contract | inline fixtures | component correctness; contract invariants | every change | must pass |
| T1 behavioural (CheckList) | template-generated, conspicuous fakes (SP 800-188) | **MFT:** every format of every entity (Safe Harbor coverage). **INV:** case, punctuation, fillers, ASR lowercase, homoglyphs, PHI across 512-token chunk boundaries, name-origin swaps, repeated or adjacent PHI. **DIR:** hotwords raise evidence; negative contexts don't redact. | every change (fast: rules and registry); nightly (with OBI) | G5 |
| T2 prompt suite | curated fabricated prompts with expected inventories (§10) | expected vs actual; stage attribution; regression baseline | every change (fast subset), nightly | G4 |
| T3 dataset evaluation | `cadence_dev` / `cadence_test` / holdout (fabricated) | engineering evidence; dev-only calibration; ablations | on demand; every test-set run logged | informational |
| T4 release evaluation | **real governed Cadence data**: double-annotated, adjudicated, agreement reported, stratified | the only go-live evidence | per release candidate | G1–G3, G6 |
| T5 fault injection | synthetic | fail-closed on model load failure, timeout, oversized input, invalid config, vault down, recognizer exception, registry store down | every change | G7 |
| T6 security / privacy | synthetic sentinels | no PHI in logs, masked outputs or caches; no network egress; vault encryption and audit; missing key handled | every change | G7 |
| T7 performance / reliability | synthetic, bucketed by length | p50/p95/p99 per stage and end to end; 1× and 2× peak throughput; one-hour soak; cold start; determinism across runs and devices | nightly, each release | G8 |
| T8 production monitoring | live, no labels | detection-rate control charts per entity × modality; sentinel canaries; outbound-guard hit rate; shadow disagreement; human-review sampling | continuous | G9 |

### 9.2 Metrics

Every metric is defined in `configs/eval/metrics.yaml` and computed from the same predictions.

**Core definitions**

| Term | Definition |
|---|---|
| Core characters | Alphanumeric characters of a gold span, excluding titles, ID affixes and edge function words, as defined in the catalog. |
| Entity TP | A gold entity overlapped by a REDACT/REVIEW detection. **Full** if every core character is covered. |
| Entity FN | Otherwise. |
| FP | A detection overlapping no gold entity and no ignore region. |

**Three scoring views**

1. **Leak view:** binary character-level and token-level PHI recall. Residual PHI characters (and
   %) are measured on the *redacted output* (§12).
2. **Entity view:**
   - strict (exact span) and relaxed (any overlap / full core) recall;
   - precision and F2 per entity, subtype and risk tier.
3. **Document view:**
   - fully-sanitised document rate (no direct-identifier leak);
   - "all mentions masked" rate per entity instance.

**Utility metrics:**
- over-redaction (non-PHI characters redacted);
- spurious spans per 1,000 characters;
- the false-positive rate on negative prompts;
- `must_not_redact` violations.

**Per-recognizer metrics** (§11.2):
- contributed;
- sole catch;
- false positives contributed;
- marginal loss when ablated;
- precision of accepted evidence.

**Statistics:**
- document-level cluster bootstrap, at least 1,000 replicates, percentile 95% CI;
- Wilson intervals for proportions;
- the rule of three for zero-miss categories;
- a paired approximate-randomisation test when comparing versions;
- **support below the gate minimum is reported as "insufficient evidence"**.

**Strata:** entity, subtype, risk tier, modality (typed / note / dictation / telephone / ASR),
document type, length bucket and chunk count. Name origin is added when the data allows (fairness).

**Cross-check:** presidio-research span and token evaluators on the same predictions. Any
difference must be explained by documented definitional differences.

### 9.3 Gold and test-data management

- **Dataset manifests** (`configs/eval/datasets.yaml`) record for each dataset:
  - id, path and SHA-256;
  - **role** (`dev`, `test`, `holdout`, `suite`, `release`, `regression`);
  - provenance;
  - **PHI class** (`fabricated`, `public`, `real_governed`);
  - access class, licence and retention date.

  Calibration and rule development read the **role**, never the dataset name.
- **Annotation guidelines** (`docs/annotation_guidelines.md`, versioned) define:
  - entity boundaries (titles, affixes, edge words);
  - subtype rules;
  - ignore regions;
  - how to annotate ASR loss.
- **Release sets** are double-annotated and adjudicated, with agreement reported as pairwise F1 on
  tokens and entities.
- **Gold files carry a schema version.** Every gold change is logged with its reason. Annotation
  errors are the only valid reason; changing gold "to match the system" is never allowed.
- **Contamination prevention:**
  - exact and near-duplicate detection (shingled Jaccard or MinHash) between dev and
    test/holdout/suite/release, and between prompt-suite items and dev. CI fails above a
    threshold;
  - the evaluation ledger (below) records every frozen-test access.
- **Evaluation ledger:** `results/ledger.jsonl`, append-only. Each run records:
  - run id and timestamp;
  - config hash, code commit and model hash;
  - dataset ids and hashes;
  - headline metrics;
  - operator.
- **Datasheet per dataset:** provenance, consent or BAA basis, annotation process, agreement,
  known gaps, retention.

---

## 10. Prompt suite and test-case design

### 10.1 Format

The suite lives in `eval_suites/english_v1/prompts.jsonl`, one prompt per line. Spans are anchored
by text, so no offsets are typed by hand.

```json
{"prompt_id": "C-007", "group": "C_conversational", "modality": "telephone", "holdout": false,
 "text": "Her birthday is April twelve nineteen eighty-five and her number is four one five five five five zero one nine eight.",
 "registry": null,
 "expected": [
   {"entity_type": "DOB", "subtype": "SPOKEN_DOB", "match": "April twelve nineteen eighty-five", "occurrence": 1,
    "expected_action": "REDACT", "expected_sources": ["DETERMINISTIC", "CONVERSATIONAL"]},
   {"entity_type": "PHONE", "subtype": "SPOKEN_PHONE", "match": "four one five five five five zero one nine eight",
    "expected_action": "REDACT"}],
 "must_not_redact": ["birthday"],
 "asr": null,
 "status": "expected_pass", "notes": ""}
```

**How the format works:**
- The loader resolves `match` and `occurrence` to `[start, end)`. It fails on zero or ambiguous
  matches, and freezes the result as `prompts.resolved.jsonl` with a hash.
- **`must_not_redact`** lists non-PHI anchors that must stay untouched.
- **`asr`** (ASR prompts only) records the clean source and the PHI the ASR destroyed:
  `{"source_text": "...", "lost": [{"entity_type": "MEMBER_ID", "source_match": "..."}]}`.
  This drives the `ASR_destroyed` outcome.
- **`status`** is one of `expected_pass`, `known_failure` (with a reason) or `exploratory`.
- **`holdout: true`** marks about 25% of prompts. These are excluded from debugging views and
  reported separately.

### 10.2 Groups

The groups follow the requested priority order. About 200 fabricated prompts in v1.

| Group | ~Count | Content |
|---|---|---|
| A single-entity | 50 | each entity and subtype in each written form (e.g. PHONE with dashes, dots, spaces, parentheses, no separators, +1), one entity per prompt |
| B multi-entity clinical | 25 | notes and summaries with 3–8 entities; adjacent entities; punctuation boundaries; repeated PHI |
| C conversational | 25 | spoken DOB / phone / ZIP / email / addresses; ordinal dates; read-backs; self-corrections |
| D ASR-style | 25 | lowercase, no punctuation, fillers, "slash", split digits, misheard tokens; with `asr.source_text` and `lost` where structure is destroyed |
| E contextual | 20 | providers, relatives (spouse / parent / child / caregiver), employers and facilities **not** in any registry; targets OBI |
| F registry-scoped | 20 | scoped fabricated registry (member, spouse, member ID, DOB, phone); **decoy** prompts naming a different member's registered values, which must not produce registry evidence |
| G edge cases | 25 | initials; hyphenated and apostrophe names; similar names; multi-format dates; long addresses; PO boxes; mixed letter-digit IDs; PHI across sentence boundaries; PHI next to punctuation; PHI repeated many times |
| N negative / non-PHI | 25 | doses, vitals, counts, durations, drug names, eponyms, years alone, habitual weekdays, organisation name, generic roles; expected PHI is empty |

**Integrity rules:**
- Expectations are written **before** the engine is run.
- After a run, an expectation may change only to fix an annotation error. The change is logged in
  `eval_suites/english_v1/CHANGELOG.md`.
- The suite is never used to tune thresholds or develop rules. Fixes are developed on dev data
  (§13).

### 10.3 Expected vs actual record

**Per expected entity, an `EntityEvaluation` record holds:**
- prompt id, entity type and subtype, expected span and text, expected action;
- `detection_status`: `detected_full`, `detected_partial` or `not_detected`;
- `redaction_status`: `redacted_ok`, `redaction_failure` or `n/a`;
- post-redaction residual characters;
- `failure_reason` and `failed_stage` (§11);
- the responsible evidence: recognizers and source groups, raw scores, score types, thresholds
  with their source, final decision and reason;
- expected sources vs actual sources.

**Unexpected detections become FP records** with recognizer, group, score, reason and any
`must_not_redact` hit.

**Per prompt, a `PromptRecord`** lists the input, expected, detected, redacted, missed and
unexpected entities, with the responsible recognizer and model, raw scores, thresholds, final
decisions and failure reasons.

---

## 11. Failure-analysis strategy

### 11.1 Stage decision tree

Every gold entity receives exactly one outcome and one failed stage. The checks run in this order.

| # | Outcome | Condition | Failed stage |
|---|---|---|---|
| 1 | `ASR_destroyed` | listed in `asr.lost`; no span exists in the transcript | ASR |
| 2 | `disabled_only` | only evidence comes from recognizers, models or normalizers disabled in this configuration | configuration |
| 3 | `policy_rejected` | evidence at or above threshold, but entity disabled, allow-listed, or REVIEW not redacted by policy | policy |
| 4 | `normalization_failure` | the only evidence comes from a derived view and was rejected for unsafe mapping, or was projected to a span not overlapping the entity | normalization |
| 5 | `below_threshold` | live evidence exists but all of it is below threshold (best rejected recognizer, raw score, threshold and source reported) | threshold |
| 6 | `no_candidate` | no evidence overlaps the entity | recognition |
| 7 | `reconciliation_failure` | accepted evidence fully covers the core, but no final detection does | reconciliation |
| 8 | `partial_span` | detections cover only part of the core characters (sub-cause: model boundary, rule boundary or word expansion) | recognition boundary |
| 9 | `redaction_failure` | detections fully cover the entity, but the redacted output still exposes core characters (§12) | redaction |
| 10 | `success` | detected in full and redacted correctly | – |

**Required changes:**
- Outcomes 4 and 9 are new.
- For outcome 4, evidence metadata must keep the derived-view span and a mapping-validity flag.
- **Known limitation:** a normaliser that *should* have fired but didn't looks like `no_candidate`.
  The local report therefore shows the derived text view for each prompt to make this diagnosable.

### 11.2 Source attribution

**Source groups** are derived from configuration:

| Group | Recognizers |
|---|---|
| `DETERMINISTIC` | `rule_*` and pattern recognizers |
| `CONVERSATIONAL` | `conv_*` |
| `REGISTRY` | `registry_*` |
| `MODEL:<key>` | e.g. `MODEL:obi_roberta` |
| `PRESIDIO` | `presidio_*` |

Each accepted evidence item also records its **view**: original, or normalised with the
normaliser ids.

**Per entity and per FP, the evaluator records:**
- the contributing groups;
- **sole catch**: only one group caught it, so it would leak without that group;
- the group whose evidence set the final type;
- **expected vs actual source**, checked against the catalog's responsibility matrix.

**Marginal ablation:** the suite is re-scored with each group disabled, reusing the collected
evidence, so it is cheap. The result shows which entities each group *uniquely* protects and which
false positives it adds, per entity type.

### 11.3 Triage workflow

Each miss is classified as one of:
- a system error;
- an annotation error (which goes to the gold changelog);
- out of scope.

**For system errors:**
1. Write a similar but **different** fabricated example into the dev set.
2. Develop the fix there and log it in `docs/rule_changelog.md`.
3. Add the original case to the prompt suite as `known_failure`, flipping it to `expected_pass`
   once it is fixed on independent evidence.

### 11.4 Explainability

**The report shows for every entity:**
- the text views;
- every candidate (recognizer, raw score, score type, threshold and its source, accepted or
  rejected with reason);
- the decision reason;
- the confidence class.

**In production:** traces carry ids, counts and timings only. They never contain text.

---

## 12. Redaction and residual-PHI evaluation

1. **Detection status** comes from the `DetectionResponse` alone.
2. **Redaction validation** comes independently from the `RedactionResponse`:
   - every core character lies inside a `Replacement` original range;
   - the output at `output_start`–`output_end` equals the replacement token and contains no core
     character;
   - a vault round trip restores the original exactly;
   - tokens are not derived from PHI (format and randomness check);
   - consistency scope behaves as configured.
3. **Post-redaction re-scan** of the sanitised text:
   - verbatim search for every gold surface;
   - normalised search (digits only for phones and IDs; case- and space-folded for names and
     emails) to catch leaks through a different mention;
   - the **outbound-guard scan** (the same component as production: rules plus registry);
   - an optional second engine pass, reported for information only;
   - the `must_not_redact` anchors must still be present.
4. **Residual and over-redaction metrics:**
   - expected, redacted and residual PHI characters (and %) per entity, document, risk tier and
     modality;
   - verbatim leaks;
   - over-redacted characters;
   - `must_not_redact` violations;
   - fully-sanitised documents with CI.
5. **Fault test:** an injected faulty operator must be reported as `redaction_failure`.
6. **Utility, later:** the downstream task delta on sanitised vs original text.

---

## 13. Regression strategy

**Baselines** are recorded per release and stored as versioned JSON stamped with the config hash:
- per-entity outcomes on T1 and T2;
- per-prompt FP counts;
- T3 headline metrics with CIs;
- T7 percentiles.

**Regression rules:**
- **zero new misses in direct-identifier entities** (G4) unless individually signed off;
- no new redaction of negative or `must_not_redact` anchors;
- no residual increase beyond the CI;
- improvements on `known_failure` are reported, and the baseline is updated only by explicit
  command after review.

**CI tiers:**

| Tier | Contents | Runs |
|---|---|---|
| Fast | T0, T1 and T2 with rules and registry only, T5, T6 (no model weights) | every change |
| Model | T1 and T2 with OBI, T3 | nightly |
| Release | T4, T7, gate runner | per release candidate |

**Re-validation triggers** are enforced by the gate runner by comparing stamped hashes:
- any config hash change;
- a model, tokenizer, Presidio or transformers version change;
- an ASR engine or ASR version change;
- a new document type or line of business;
- a drift alarm;
- a confirmed leak;
- at least once a year.

**Failures become permanent regression cases.** Every confirmed production or manual-evaluation miss
becomes a fabricated `known_failure` prompt plus a dev example, and stays in the suite for good.

---

## 14. Privacy and security controls

| Control | Implementation | Basis |
|---|---|---|
| PHI-free logs, enforced | structured logging with an allow-listed field set; a log filter rejecting text-bearing records; sentinel-PHI grep tests across all logs and masked outputs | SP 800-53 AU / SI-11 [V] |
| Evaluation artifact classification | real-governed data outputs masked by default, written under an access-controlled root, retention date from the manifest, a `purge` command; fabricated data may show text | HIPAA minimum necessary; SP 800-188 |
| Keyed hashes only | text hashes in logs and caches use HMAC with a managed key | brute-force risk on short values |
| Vault | encrypted at rest (AES-GCM); key from KMS or env with a rotation procedure; scoped consistency; audited restore (vault id, caller, time, no content); TTL and deletion | 45 CFR 164.514(c) [V]; SP 800-188 [V] |
| Keys | no random fallback: a required key that is missing fails closed; FF1 only if format preservation is ever required (domain ≥ 10⁶) | SP 800-38G r1 [V] |
| No egress | sockets blocked in tests; `OFFLINE_MODE` enforced; models loaded from local cache with hash verification; no LLM, cloud or AHDS components; `log_decision_process` never enabled | organisational requirement; Presidio notes |
| Supply chain | locked, hash-pinned dependencies; SBOM; licence check of model artifacts | SP 800-53 SR |
| Access | documented roles for restore and for evaluation outputs; tiers T0–T3 and T5–T7 never need production PHI | 45 CFR 164.312(a)(b) [V] |
| Fail-safe | the §6.5 failure policy, proven by T5 | SP 800-53 SC-24 / SI-17 [V] |

---

## 15. Production-readiness gates

The gates live in `configs/eval/gates.yaml`. `python -m harness_eval.gates run` produces a go/no-go
report. The **numbers are proposals for the risk owner**; no standard prescribes them (§2.7).

| Gate | Metric (T4 real governed data unless noted) | Proposed criterion |
|---|---|---|
| G1 direct identifiers | character-level recall per entity × modality, **95% CI lower bound** | ≥ 0.99 notes / typed; ≥ 0.97 ASR. At least 300 mentions per category, otherwise "insufficient evidence", which counts as no-go. |
| G2 quasi identifiers | recall lower bound per entity | ≥ 0.95 |
| G3 documents | fully-sanitised rate (no direct-identifier leak) with CI | ≥ 0.99 |
| G4 regression | new direct-identifier misses vs previous release (T1–T4) | 0, or individually signed off |
| G5 behavioural | T1 MFT pass rate | 100%; INV failures triaged |
| G6 utility | over-redaction rate; negative-suite FPs | at or below the agreed limit (e.g. ≤ 3% of non-PHI characters) |
| G7 fail-closed and security | T5 + T6 pass rate | 100% |
| G8 performance | p95 / p99 per typical document; soak memory stability | within SLO (to be set; e.g. p95 ≤ 500 ms on CPU per typical call) |
| G9 operations | monitoring baselines exported; canaries configured; system card issued | present |
| **Overall** | minimum across gates (ML Test Score style) | any failure = **no-go**; sign-off by an independent reviewer and the privacy officer |

---

## 16. Implementation phases

| Phase | Content | Gaps closed | Exit criteria |
|---|---|---|---|
| **P0 Foundations** (Critical) | git repository; locked, hashed dependencies; code-commit stamping; fail-closed handler with deadlines, input-size limit and startup self-test; T5 fault-injection suite; enforced PHI-free logging and T6 tests; remove the pseudonym-key fallback | G-01, G-02, G-14, G-23, part of G-04 | T5 and T6 green; every response stamped with commit, config and model hashes |
| **P1 Configuration and entity catalog** | layered configuration with loader, strict schema, environment overlays and provenance; **entity catalog**; declarative pattern recognizers; hard-coded tables moved to config; modality thresholds; migration plus equivalence test; matrix generated from the catalog | G-06, G-08, G-16, G-22, part of G-07 | old and new configuration give identical detections on the regression corpus; adding a pattern-based entity needs zero code (proven by test) |
| **P2 Evaluation core** | manifests, roles, contamination checks and ledger; scorer v2 (three views, CIs, strata, paired tests); new outcomes and stage tree; independent redaction validation and re-scan; source-group attribution and ablation; presidio-research cross-check | G-09, G-10 (tooling), G-12, G-13, G-25 | metric tests match hand-computed fixtures; cross-check agreement documented |
| **P3 Test suites** | CheckList generator (T1); prompt suite v1 (T2, about 200 prompts, 25% holdout); performance and soak harness (T7); cross-device determinism | G-11, G-15, G-21 | suites versioned, baselined, running in CI |
| **P4 Gates, regression and documentation** | `gates.yaml` and gate runner; regression baselines and CI tiers; re-validation triggers; system card and datasheet generators; runtime/evaluation package split | G-03 (tooling), G-17, G-19 | an end-to-end go/no-go report on T3, explicitly labelled "engineering evidence, not release evidence" |
| **P5 Production readiness** | hardened vault (encryption, persistence, audit, scope); outbound guard; model-hash verification; shadow mode; monitoring hooks and canaries; **real governed release dataset** (guidelines, double annotation, agreement) and first T4 gate run; new Safe Harbor entities developed on dev | G-04, G-05, G-07, G-18, G-20, G-03 (evidence) | all gates evaluated on T4; sign-off |

P0 and P1 come first. P2 and P3 can then run in parallel. The real-data workstream in P5 needs
governance and annotation resources and should **start immediately**, because it is the critical
path to any go-live claim.

---

## 17. Files and modules to create or modify

### 17.1 Create

| Path | Responsibility |
|---|---|
| `configs/phi/**` (§6.2) | runtime configuration: catalog, recognizers, models, normalization, reconciliation, policy, environments, generated |
| `configs/eval/{datasets,suites,gates,metrics,presets}.yaml` | evaluation-only configuration |
| `harness/phi/config/schema.py`, `loader.py`, `migrate.py`, `__main__.py` | Pydantic schema; composition and overlays with restrictions; resolved hash; `validate` and `migrate` commands |
| `harness/phi/catalog.py` | catalog API (risk tier, actions, core-character rules, subtypes, Safe Harbor mapping, outbound-guard behaviour) |
| `harness/phi/recognizers/patterns.py` | declarative pattern recognizers and named validators |
| `harness/phi/failsafe.py` | fail-closed handler, deadlines, input guards, startup self-test |
| `harness/phi/telemetry.py` | allow-listed structured logging, PHI log filter, metrics counters, keyed hashing |
| `harness/phi/vault.py` | vault interface; encrypted file and in-memory backends; scope, TTL, audit |
| `harness/phi/outbound_guard.py` | independent re-scan of sanitised payloads (block or flag) |
| `harness_eval/__init__.py` | evaluation package; existing evaluation modules move here |
| `harness_eval/manifests.py`, `contamination.py`, `ledger.py` | dataset roles and hashes; near-duplicate checks; append-only run ledger |
| `harness_eval/scoring.py` | three-view metrics; cluster bootstrap and Wilson CIs; strata; paired approximate randomisation |
| `harness_eval/attribution.py` | stage decision tree; source groups; sole catch; ablation |
| `harness_eval/redaction_check.py` | independent redaction validation and post-redaction re-scan |
| `harness_eval/checklist.py`, `suites/behavioural/*.yaml` | behavioural templates (MFT/INV/DIR) and generator |
| `harness_eval/suite.py`, `eval_suites/english_v1/{prompts.jsonl,registry/,entity_coverage.yaml,baseline.json,CHANGELOG.md,README.md}` | prompt suite: loader, validator, runner, compare, baseline/regress |
| `harness_eval/perf.py`, `harness_eval/faults.py` | latency, throughput, soak and cold-start harness; fault-injection scenarios |
| `harness_eval/gates.py`, `harness_eval/cards.py`, `harness_eval/presidio_xcheck.py` | gate runner and re-validation triggers; system card and datasheet generation; metric cross-check |
| `docs/annotation_guidelines.md`, `evaluation_methodology.md`, `release_gates.md`, `config_guide.md`, `security_controls.md`, `system_card.md` (generated) | documentation |
| tests: `test_config_schema.py`, `test_config_migration.py`, `test_catalog.py`, `test_patterns_declarative.py`, `test_failsafe.py`, `test_telemetry_privacy.py`, `test_vault.py`, `test_outbound_guard.py`, `test_scoring_ci.py`, `test_attribution.py`, `test_redaction_check.py`, `test_contamination.py`, `test_gates.py`, `test_checklist_fast.py`, `test_suite_regression_fast.py`, `test_*_model.py` (marker `model`), `test_perf_smoke.py` | coverage for every new component |

### 17.2 Modify

| Path | Change |
|---|---|
| `harness/phi/engine.py` | use the loader; fail-safe wrapper; stamping (commit, model hash, catalog version); shadow models; telemetry; vault interface; outbound-guard hook |
| `harness/phi/contracts.py` | additive: stamping fields, fail-closed response status, new outcomes, `subtype`, modality-aware threshold source, `PromptRecord`, `EntityEvaluation` |
| `harness/phi/policy/__init__.py` | thresholds by modality; entity actions from the catalog; exclusions file |
| `harness/phi/recognizers/{rules,registry,conversational}.py` | read entity tables, keywords, guards and matching parameters from config |
| `harness/phi/normalizers/__init__.py`, `recognizers/base.py` | keep the derived-view span and a mapping-validity flag in evidence metadata |
| `harness/phi/redaction/__init__.py` | vault abstraction; consistency scope; no key fallback |
| `harness/phi/{evaluate,calibrate,compare,analyze,datasets,report,matrix}.py` | move to or wrap from `harness_eval`; read manifest roles; write ledger entries |
| `harness/evaluation/matching.py` | core-character rules from the catalog (legacy benchmark keeps working) |
| `configs/phi_harness.yaml` | kept as the legacy entry point; superseded by `configs/phi/phi.yaml` |
| `README.md`, `docs/engine_contract.md`, `docs/manual_evaluation.md`, `docs/presidio_architecture.md` | updated |

---

## 18. Testing and validation plan

1. **Unit and contract (T0):**
   - schema rejections (unknown keys or ids, out-of-range values, forbidden prod overrides);
   - catalog-driven behaviour, including a test that **adds a fabricated pattern entity in a
     temporary config with no code change**;
   - metric fixtures with hand-computed CIs;
   - each stage-tree outcome built from constructed evidence;
   - an injected faulty operator detected as `redaction_failure`;
   - vault encryption, scope and audit;
   - log-filter rejection.
2. **Integration:**
   - old and new config layouts are equivalent on the regression corpus;
   - engine plus outbound guard blocks an injected leak;
   - the runtime cannot import `harness_eval` (an import-linter rule);
   - registry scoping with decoy subjects.
3. **End to end:**
   - the full CLI path (analyze → suite → gates) on fabricated data produces every report and a
     go/no-go document labelled "engineering evidence";
   - batch vs single-document consistency;
   - determinism across runs, plus a CPU vs MPS comparison.
4. **Behavioural (T1) and prompt suite (T2):** versioned and baselined, with fast tiers in CI.
5. **Fault injection (T5):** for every scenario, no LLM call and no pass-through. The response
   carries a fail-closed status and no PHI.
6. **Security (T6):**
   - sentinel PHI is absent from logs, masked outputs and caches;
   - sockets are blocked;
   - a missing key fails closed;
   - the vault is unreadable without its key.
7. **Performance (T7):** latency percentiles by stage and length bucket, 2× peak load, one-hour
   soak and cold start, each stored as a baseline.
8. **Synthetic smoke test:** the end-to-end example plus the fast tier of the prompt suite.
9. **Release validation (T4):** on governed real data only, under the gate runner, with independent
   review. **Nothing before T4 is presented as release evidence.**

All existing tests (132 today) must keep passing at every phase.

**Commands after implementation**

```bash
python -m harness.phi.config validate --env prod                       # resolve, validate, print resolved hash
python -m harness.phi.analyze --input sample.txt --redact              # single document (existing)
python -m harness.phi.analyze --input-dir data/manual_eval --gold gold.jsonl --redact --output results/me
python -m harness_eval.suite validate --suite eval_suites/english_v1
python -m harness_eval.suite run --suite eval_suites/english_v1 --redact --output results/suite/run1
python -m harness_eval.suite ablate --suite eval_suites/english_v1 --output results/suite/ablation
python -m harness_eval.suite regress --suite eval_suites/english_v1    # regression check vs baseline
python -m harness_eval.checklist run --output results/checklist
python -m harness_eval.faults run
python -m harness_eval.perf run --scenario typical_call --output results/perf
python -m harness_eval.gates run --release-set <id> --output results/gates/rc1   # go / no-go report
pytest -q                 # fast tiers (no model weights)
pytest -q -m model        # model tiers (local OBI)
```

---

## 19. Decisions required

| # | Decision | Owner | Recommendation |
|---|---|---|---|
| 1 | Gate numbers G1–G8 and minimum support per category | risk owner / privacy officer | start from §15; review after the first T4 run |
| 2 | Fail-closed mode: block the LLM call, or a conservative fallback (rules + registry masking + flag) | product + privacy | **block** for detector, timeout and vault failures; configurable per failure class |
| 3 | Token consistency scope | privacy | per conversation; never global (Azure; Osborne 2025) |
| 4 | Vault persistence technology and key management | platform / security | encrypted store behind the vault interface; KMS-managed keys; audited restore |
| 5 | New canonical entities (SSN, HEALTH_PLAN_ID via MEMBER_ID subtype, LICENSE_CERT_ID, VEHICLE_ID, DEVICE_ID, IP_ADDRESS, AGE_OVER_89) | privacy + engineering | add to the catalog disabled; enable after dev development and behavioural tests |
| 6 | Real governed data for T4: source, volume (at least 300 mentions per direct category per modality), annotation resources, agreement target | compliance / data governance | **start now**; critical path |
| 7 | Whether the LLM payload needs Expert Determination | legal | out of engineering scope; to be raised |
| 8 | Prompt-suite authorship | engineering + reviewers | engineering drafts about 200 fabricated prompts; reviewers approve; frozen before the first run |
| 9 | Timing of the `harness_eval` package split | engineering | in P4, to avoid churn during P1–P3 |

---

## 20. References

`[V]` = read; `[S]` = search snippet or secondary only.

**Standards, regulation and guidance**
- NIST. *AI Risk Management Framework 1.0* (NIST AI 100-1), 2023; Playbook, MEASURE. https://airc.nist.gov/airmf-resources/playbook/measure/ [V]
- NIST. *AI 600-1 Generative AI Profile*, 2024. https://doi.org/10.6028/NIST.AI.600-1 [S]
- IMDRF / FDA / Health Canada / MHRA. *Good Machine Learning Practice: Guiding Principles* (N88, 2025; original 2021). https://www.fda.gov/medical-devices/artificial-intelligence-enabled-medical-devices/good-machine-learning-practice-medical-device-development-guiding-principles [V date and agencies only]
- ISO/IEC 42001:2023; ISO/IEC 25059:2023 [S]
- CHAI. *Assurance Standards Guide* (draft, 2024) [S]; *Applied Model Card* draft v0.1, 2024. https://mc.chai.org/v0.1/documentation.pdf [V]
- ASTP/ONC HTI-1 (2023) and HTI-5 proposed rule [S]
- 45 CFR 164.514. https://www.law.cornell.edu/cfr/text/45/164.514 [V]
- HHS OCR. *Guidance Regarding Methods for De-identification of PHI*, 2012 [S]
- 45 CFR 164.312. https://www.law.cornell.edu/cfr/text/45/164.312 [V]; HIPAA Security Rule NPRM (Jan 2025) status [S]
- NIST SP 800-188, *De-Identifying Government Datasets*, 2023. https://csrc.nist.gov/pubs/sp/800/188/final [V]
- NIST IR 8053, 2015 [S]; ISO/IEC 20889:2018 [S]; ISO 25237:2017 [S]; HITRUST De-Identification Framework [S]; UK ICO anonymisation guidance, 2025 [S]
- NIST SP 800-38G Rev.1, 2nd public draft (FF1), 2025. https://csrc.nist.gov/pubs/sp/800/38/g/r1/2pd [V]; NIST SP 800-57 Pt.1 Rev.5 [S]
- NIST SP 800-53 Rev.5: SC-24, SI-11, SI-17 [V]; AC, AU, SC, SI, CM, SA, SR families [S]
- Federal Reserve / OCC SR 11-7 (2011) and its 2026 replacement [S]

**Vendor and tool documentation**
- Google Sensitive Data Protection (hotword and likelihood rules, exclusion rules, InfoType version, templates) and Healthcare API de-identify. https://docs.cloud.google.com/sensitive-data-protection/docs/ [V]
- AWS Comprehend Medical DetectPHI. https://docs.aws.amazon.com/comprehend-medical/latest/dev/textanalysis-phi.html [V]
- AWS Transcribe PII redaction. https://docs.aws.amazon.com/transcribe/latest/dg/pii-redaction.html [V]
- Microsoft Azure Health Data Services de-identification: overview and Transparency Note. https://learn.microsoft.com/en-us/azure/healthcare-apis/deidentification/ [V]
- Microsoft Presidio 2.2.364 (source and docs, data-privacy-stack/presidio) [V]; presidio-research evaluators. https://github.com/data-privacy-stack/presidio-research [V]
- John Snow Labs DeIdentification [S]; Philter-UCSF. https://github.com/BCHSI/philter-ucsf [V]; Private AI [S]; Tonic Textual [S]
- Hydra defaults list. https://hydra.cc/docs/advanced/defaults_list/ [V]

**Literature**
- Stubbs A, Kotfila C, Uzuner Ö. Overview of the 2014 i2b2/UTHealth de-identification track. *JBI* 58 (2015) [V]
- Stubbs A, Uzuner Ö. Annotating longitudinal clinical narratives for de-identification. *JBI* 58 (2015) [V]
- Yang H, Garibaldi JM. Automatic detection of PHI from clinic narratives. *JBI* 58 (2015) [S]
- Dehghan A, et al. Combining knowledge- and data-driven methods for de-identification. *JBI* 58 (2015) [V]
- Dernoncourt F, et al. De-identification of patient notes with recurrent neural networks. *JAMIA* 24(3), 2017 [S]
- Norgeot B, et al. Philter. *npj Digit Med* 3:57, 2020 [V]
- Kim Y, Heider PM, Meystre SM. Ensemble-based de-identification. *AMIA* 2020 [V]
- Kocaman V, et al. Beyond Accuracy: automated de-identification of large real-world clinical text. ML4H 2023 [V]
- Fernandes AC, et al. De-identification for a mental-health case register (CRIS). *BMC MIDM* 13:71, 2013 [V]
- Lee JY, et al. Feature-augmented neural networks for de-identification. ClinicalNLP 2016 [V]
- Yang X, et al. Deep learning de-identification in cross-institute settings. *BMC MIDM* 19, 2019 [V]; sample-size study, medRxiv 2020 [V]
- Cohn I, et al. Audio De-identification: a new entity recognition task. NAACL Industry 2019 [V]
- Carrell D, et al. Hiding in plain sight. *JAMIA* 20(2), 2013 [V]; resilience to hostile re-identification. *JAMIA* 27(9), 2020 [V]
- Osborne JD, et al. Markov chain replacement for surrogate identifiers. *Electronics* 14(19), 2025 [V]
- Alrazihi LA, et al. Accuracy of automated anonymization tools. *Surg Neurol Int* 16:313, 2025 [S]
- Ribeiro MT, et al. Beyond Accuracy: Behavioral Testing of NLP Models with CheckList. ACL 2020 [V]
- Breck E, et al. The ML Test Score. IEEE BigData 2017 [V abstract]; Sculley D, et al. Hidden Technical Debt in ML Systems. NeurIPS 2015 [S]
- Mitchell M, et al. Model Cards for Model Reporting. FAT* 2019 [S]; Gebru T, et al. Datasheets for Datasets. *CACM* 2021 [S]
- Hanley JA, Lippman-Hand A. If nothing goes wrong, is everything all right? *JAMA* 249, 1983 [S]
- Dror R, et al. Testing statistical significance in NLP. ACL 2018 [S]
- Beyer B, et al. *Site Reliability Engineering*, Google, 2016 [S]
