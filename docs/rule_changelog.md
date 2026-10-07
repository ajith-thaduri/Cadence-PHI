# Rule, normaliser and scoring changelog

Every change states the data it was developed on. **Nothing below was developed, tuned or
debugged on test data** (`cadence_text`, `cadence_asr`, `meddocan_test`, `synthetic_test`).
Development data for this round: `cadence_dev_text` (52 docs, 1,127 PHI spans) and
`cadence_dev_asr` (its spoken documents through TTS → telephone channel → Whisper-small).
They were written by separate authoring agents, from the same specification as the test set
but with different scenarios, people and identifiers. Test-set results are measured once, after
the dev freeze. Held-out results are in `results/phi/` and the final report.

Policy: all conversational recognizers are opt-in (`enabled: false` until evaluated on dev).
Each has unit tests in `tests/test_phi_harness.py`, including negatives taken from the dev
false positives.

---

## Scoring refinement D3: edge function words excluded from gold spans

- **What changed.** Lowercase articles and prepositions at the edge of a gold span are dropped
  before scoring. Examples: "the" in "the twenty-third", "in" in "in August", "el" in
  "el lunes". The word list is `harness/phi/evaluate.py: EDGE_WORDS`.
- **Why.** These words identify no one. Before this change, a shield that removed
  "twenty-third" but left "the" was charged with leaking PHI. This is the same principle as the
  existing exclusion of titles and ID affixes ("Dr.", "MRN").
- **Scope.** 39 of 1,127 dev spans change. The refinement applies identically to every
  configuration, model and dataset. Phase-3 decision results used the old definition and are
  not directly comparable.

## Registry: match type recorded (`harness/shield/registry.py`)

- Each match is now labelled `registry:<kind>:<exact|fuzzy>[:single_token]`.
- Fuzzy matches score ×0.9, so `registry_exact` and `registry_fuzzy` are separate score types.
- **Developed on:** `synthetic_dev` (behaviour-preserving refactor; `tests/test_shield.py` unchanged).

## Normaliser `spoken_email` v2 (`harness/phi/normalize.py`)

- **Purpose.** Recover email addresses read aloud.
- **What changed.** The local part is now collected backwards from "at" / "arroba" (at most
  14 tokens). It can include:
  - spelled letters, including Spanish letter names
  - "double r"
  - digit words and tens ("nine one", "ochenta y ocho")
  - "dot" / "punto" / "underscore"
- **Where it stops.** Stop words ("is", "email", "to", "me", …), sentence breaks and speaker
  turns. Commas are allowed only between letter-by-letter or digit-by-digit tokens.
- **Developed on:** `cadence_dev_text`. Dev examples: "priya dot raman nine one at example
  dot net", "agnes w o at …", "jota ge punto u zeta ce a te e ge u i arroba …".
- **False-positive risk.** "met at example dot com" style phrases. These are rare because the
  domain must end in a known TLD.
- **Tests:** `test_spoken_email_local_parts`, `test_normalizers`.

## Conversational recognizers (`harness/phi/conversational.py`)

**Developed on:** `cadence_dev_text`, then checked on `cadence_dev_asr`. All score `rule_match`,
with raw scores 0.6–0.85 used only for type precedence.

| recognizer | targets (dev examples) | FP risk and guard | tests |
|---|---|---|---|
| `conv_spoken_id` | Read-aloud IDs: letter prefixes ("C, D, four four seven…", "B L, L as in Lima…"), Spanish letter names ("eme de…"), tens groups ("sesenta y tres"), fillers, self-corrections ("…two, sorry, scratch that, nine one three"). Labelled by nearest keyword (phone / MRN / member / account / NPI, else OTHER_UNIQUE_ID). | Vitals, doses, lab values and clock times. **Guards:** ≥2 *spoken* digit words required (written numerals belong to the written rules); time contexts excluded ("a.m.", "a las", "de … a"); ambiguous one-letter words (a, e, o, y, de) never count as letters; bare digit runs need an ID keyword or ≥7 digits; speaker labels removed from context ("MEMBER:" is not a member-ID cue). | `test_conv_spoken_id`, `test_conv_spoken_id_negatives` |
| `conv_partial_phone` | "ends in oh one five two", "termina en cero uno dos ocho", "last four … two eight eight one" | Low: needs an explicit trigger phrase plus exactly 4 spoken digits | `test_conv_partial_phone_and_dates_and_address` |
| `conv_ordinal_date` | Ordinal days ("the twenty-third", "Tuesday the fourteenth"); Spanish weekday + day ("sábado diecisiete", "el jueves que viene"); spoken numeric DOB after a DOB cue ("four, twelve, ninety-one"); spoken years ("once de marzo del setenta y ocho") | "the second dose", "the first time". **Guards:** a stop-list of following nouns; bare first/second/third need a date cue ("on the", "of <month>", "is the"), including normalised "1st/2nd/3rd". Habitual weekdays ("los lunes") are not matched. | same |
| `conv_spoken_address` | Unit/apt/suite/lote/box numbers (keyword must be a whole word), spoken house number + street ("twenty-six oh one Marigold Lane"), Spanish "N de (la calle) X", "calle X, el N", spoken ZIP after a state name | "step" (fixed: word boundary), "a unit of insulin". unit/box/lot/space need an address context within 160 chars. | same |
| `conv_spelled_name` | Spellings of ≥4 letters ("R A M A N", "g, h, e, double r…"). Label: EMAIL near "at" / "email", ADDRESS near street words, else NAME. | Clinical acronyms spelled in dictation ("D N R", "P E G", "F V C"): minimum 4 letters; letters inside an ID run are skipped. | `test_conv_language_and_spelled` |
| `conv_language` | The member's preferred language: "prefers Spanish", "Pref lang English", "Idioma preferido: español", a one-word turn answer ("Español."), "send it in Spanish" | Staff descriptions ("Spanish-speaking nurse") and interpreter option lists ("intérprete de quechua o español"). Context must be in the same sentence; "speaks/habla" are not cues, because they describe other people. | same |

### Dev results (`cadence_dev_text`, config thresholds, OBI + rules + registry + normalisers)

| configuration | mean residual | high-risk full recall | span precision | spurious spans |
|---|---|---|---|---|
| baseline (no conversational rules) | 14.04% | 78.9% | 90.2% | 116 |
| + conv_spoken_id | 10.49% | 83.6% | 90.3% | 115 |
| + conv_ordinal_date | 12.43% | 82.5% | 89.6% | 125 |
| + conv_spoken_address | 12.22% | 81.5% | 90.0% | 119 |
| + conv_language | 13.39% | 78.9% | 90.3% | 116 |
| + conv_partial_phone | 13.90% | 79.2% | 90.2% | 116 |
| + conv_spelled_name | 13.86% | 79.2% | 90.2% | 116 |
| + all six | 6.37% | 89.7% | 89.8% | 127 |

(The table was measured before the `spoken_email` v2 change and the bare-ordinal fix.
Final dev and held-out numbers are in the run outputs.)

---

## 2026-10-05 (later): engine v2 (contract refactor) and enabling the conversational rules

### Conversational recognizers enabled by default

They were evaluated on both dev splits through the v2 engine (`results/phi/v2_dev_default` vs
`results/phi/v2_dev_obi_full`):

| dev split | conversational rules off | on |
|---|---|---|
| `cadence_dev_text` mean residual | 13.4% | 5.7% |
| `cadence_dev_text` high-risk full recall | 79.7% | 90.5% |
| `cadence_dev_text` spurious spans | 115 | 120 |
| `cadence_dev_asr` mean residual | 35.2% | 32.4% |
| `cadence_dev_asr` high-risk full recall | 64.8% | 69.8% |
| `cadence_dev_asr` spurious spans | 129 | 130 |

The gain on dev ASR is much smaller, because 184 of the 812 gold entities in `cadence_dev_asr`
(23%) never reach the transcript (`ASR_destroyed`). Mixed-language calls lose most of their
entities in Whisper. That is an ASR failure, not a detector failure.

### Registry: spoken DOB variants (`harness/shield/registry.py: _dob_variants`)

- **What changed.** The subject's DOB is now also matched in its normalised spoken forms:
  "28th March 1985", "28th of March 1985", "March 28th 1985", "March 28 1985", "28 March 1985",
  and `m/d/yy`.
- **Developed on.** The synthetic engine example (`examples/request.json`), where a spoken DOB was
  typed DATE instead of DOB. It is a general format expansion of the subject's own record, not
  tuned on any dataset.
- **False-positive risk.** Only the subject's own DOB is matched.

### Policy: type refinements (`type_refinements` in the YAML)

When accepted evidence in one group disagrees between a type and a more specific subtype of it,
the subtype wins: DATE→DOB; NAME→RELATIVE_NAME / PROVIDER_NAME / CARE_TEAM_MEMBER;
ORGANIZATION→FACILITY_NAME / EMPLOYER. This changes only the reported type, never the redaction
extent.

## 2026-10-06: format gaps found by the behavioural (CheckList) suite; Safe Harbor patterns enabled

**Source of every change below:** `eval_suites/checklist_v1/templates.yaml`. Its values are fabricated
and taken from published formats (RFC 5737 IPs, 555-01xx phones, CMS MBI, ISO 3779 VINs). Nothing here
was developed against the prompt suite (`eval_suites/english_v1`), a test split, or gold labels. The
dev split was used only to confirm that no new false positives appeared.

### Checklist generator fix (`harness_eval/checklist.py`)

- **The defect.** `INV:chunk_boundary` cut the neutral filler mid-word and glued it onto the
  template ("...and tMRN 4471-2290"). That destroyed the keyword's word boundary, so the case was
  testing a malformed input rather than chunk placement.
- **The fix.** The pad is now made of whole sentences. Engine behaviour was not changed for this.

### Keyword identifiers (`harness/shield/rules.py: _FILLER, _ID_TOKEN, keyword_ids`)

- **Bridge between keyword and value.** It now accepts a disfluency followed by a comma
  ("MRN um, 4471-2290") and an opening bracket or quote ("Member ID (ABC12345)", `ID "ABC12345"`).
- **Single-token alphanumeric values.** New alternative `_ID_TOKEN`: one token of alphanumeric
  segments, optionally joined by hyphens, containing at least one digit. Examples: `A-99231-X`, the
  CMS MBI `3RM7TK2PW48`, lowercase `hpv48213q`.
- **Choosing the value.** The longest of the grouped-digit form and the single-token form is taken,
  so "4471 2290" is unchanged. The existing rule of at least 4 digits still applies.

### Phones (`phones`)

- **Extensions.** A trailing extension (`ext. 12`, `extension 4`, `x204`) is now part of the PHONE
  span. Previously it leaked.

### ZIP (`zips`)

- **ZIP+4 after a ZIP keyword.**
- **New rule `zip_state_wrapped`.** Matches `, <state abbr> <zip>` in any case, with an optional
  disfluency or opening bracket/quote: "fresno, ca 93701", "Fresno, CA (93701-1234)". It requires
  the comma before the state abbreviation, so phrases like "in 93701" or "or 12345" stay unmatched.

### Street addresses (`addresses`)

- **New rule `street_number_abbr_lower`.** Matches `<number> <words> st|ave|rd|ln|blvd|pkwy|hwy` in
  lowercase, with a case-insensitive unit ("1182 oak ave, suite 210").
- **Deliberately excluded.** `dr`, `ct` and `pl`. In lowercase they collide with "by dr",
  counts and plurals.

### Pattern recognizers (`configs/phi/recognizers/patterns.yaml`)

- **Case-insensitive matching.** `pattern_device.serial` and `pattern_vehicle.vin` are now
  case-insensitive. The VIN check digit (ISO 3779) still gates every 17-character candidate.
- **Checklist spec correction.** The AGE_OVER_89 value "aged 101" became "101 years old". The age
  is the identifier; the word "aged" is not, and requiring it to be hidden was a spec error.

### Results

| | before | after |
|---|---|---|
| CheckList fast tier, default config | 931/1319, G5 FAIL | 1129/1319, G5 **PASS** (every enabled entity 100%) |
| CheckList fast tier, with the six patterns | n/a | **1319/1319**; DIR negatives and context-required cases clean |
| `cadence_dev_text` residual / high-risk recall / spurious | 5.87% / 92.0% / 83 | 5.83% / 92.2% / 83 |
| `cadence_dev_asr` residual / high-risk recall / spurious | 20.0% / 78.9% / 73 | unchanged |
| new dev false positives (rules; rules + patterns) | n/a | 0; 0 |

### Enabled by default

Entities SSN, IP_ADDRESS, LICENSE_CERT_ID, VEHICLE_ID, DEVICE_ID, AGE_OVER_89, together with their
`pattern_*` recognizers. They were enabled because they met the documented criteria: 100%
MFT/INV/DIR and zero new dev false positives.

**Limitation.** The dev corpus contains almost no mentions of these identifiers (2 SSN candidates).
The dev check therefore shows that the patterns are quiet on clinical text; it does not measure
their recall. Recall evidence for them comes from the CheckList only, until governed (T4) data
exists.

## 2026-10-06 (later): P4/P5 runtime changes

### Reconciliation: Safe Harbor identifier type refinements (`reconciliation.yaml`)

- **What changed.** OTHER_UNIQUE_ID → SSN, LICENSE_CERT_ID, VEHICLE_ID, DEVICE_ID, IP_ADDRESS. When accepted
  evidence in one overlap group disagrees between the generic identifier type and a more specific Safe Harbor
  category, the specific category is reported. Only the label changes, never the redaction extent.
- **Source.** A monitoring canary: "SSN 219-09-9999" was redacted but labelled OTHER_UNIQUE_ID, because the
  legacy keyword rule out-ranked `pattern_ssn`.
- **Dev check.** Strict recall on `cadence_dev_text` went from 0.8183 to 0.8195; nothing else moved.

### Model integrity: OBI weights pinned (`models.yaml`)

- **What changed.** `weights_sha256: 5571522732…` is now set; it was computed over the pinned revision's weight
  files. The engine verifies weights it loads from disk and refuses to serve on mismatch. Caller-injected adapters
  (test doubles) are recorded as `injected`. Verification costs about 0.7 s at startup.

### Serving engine model-hit cache bounded (`runtime.yaml` `model_cache.max_entries: 0`)

- **What changed.** Before this, every serving engine kept model spans for every text it saw, without limit.
  That was a memory leak, and it retained PHI-derived data. It was found by the T7 soak, where latency fell from
  1.25 s to 16 ms on repeated texts.
- **Scope.** Evaluation tools pass their own cache and are unaffected.

## 2026-10-07: detection v2 (general fixes from the 10-case strict review)

**How these changes were developed.** A strict typed review of 10 user-supplied fabricated cases (correct = fully
hidden **and** correctly typed) found 113 type errors and 12 leaks. Each failure was traced to its mechanism, and only
general fixes were made. The cases were then registered as **development** data (`user_cases_v1`, role `dev`), because
they informed the fixes; they are not a held-out measure.

Every change was validated three ways:
- the behavioural CheckList, using fresh fabricated values and new negative sentences;
- the dev split, checked for new false positives and recall regressions;
- both prompt-suite tiers.

New detectors started disabled and were enabled only after passing all three.

### Root causes and general fixes

| Root cause | General fix | Where |
|---|---|---|
| A model span bridging two deterministic findings merged them ("TX 78701" became one ZIP). | Bridged groups are split when they contain disjoint deterministic clusters of *different* types. Text between the parts stays redacted unless it is only connector words. | `reconciliation` v2, `split_bridged_groups` |
| Within one precedence level, a fragment could set the type ("10.10.10" DATE inside an IP). | The type comes from evidence that covers the whole group. | `policy.decide` |
| Dotted dates matched inside IPv4 and version strings. | Dotted dates need consistent separators and cannot be followed by `.digit`. | `rules.dates` |
| The street rule read "33101 **by Dr.**" as a street ending in Dr (Drive). | Street-name words exclude function words. | `rules.addresses` |
| "Washington, DC" was read as a state; DC was unknown. | DC added as a state. A state name followed by ", ST" is a city. Street-suffix words cannot be city tokens. State names inside street names ("Pennsylvania Avenue") or person names ("Jamal Washington") are not states. Street quadrants (NW/SE…) are included in the address. | `rules` |
| The ambiguous codes ID/IN/OR/OK/ME/HI were read wrongly in both directions. | They are a state only in address position: after "City, " and followed by a ZIP or the end of the clause. | `rules._state_code_here` |
| Specific identifiers typed only as OTHER_UNIQUE_ID (licence, DEA, billing, IBAN, beneficiary, IMEI, VIN…). | New declarative `labelled` recognizer: a field label types the value that follows it. OTHER_UNIQUE_ID is refined to ACCOUNT_ID or MEMBER_ID when evidence covers the same alphanumeric content. | `recognizers/labelled.yaml`, `type_refinements` |
| Context words were found on **both** sides, so the next field's label claimed a value (passport → SSN). | Context is left-side by default. `max_words_between` binds the cue to the value, counting content words only. | `PatternRecognizer` |
| No detector for MAC addresses, IMEI, IBAN or geo-coordinates. | `pattern_mac_address` (IEEE 802), `pattern_imei` (Luhn, with a cue), `pattern_iban` (ISO 13616 mod-97), and `pattern_geo_coordinates` (decimal pairs with a location cue, or hemisphere notation). New catalog entity **GEO_COORDINATES** (direct; outbound guard blocks it). | `patterns.yaml`, `entities.yaml` |
| The model tagged field-label acronyms as PHI ("MAC" as FACILITY_NAME, "IP 10.…"). | Field labels are trimmed from span edges, and label-only spans are dropped, unless a deterministic recognizer typed the token (Idaho's "ID"). | `reconciliation.field_labels` |
| Fragments of one identifier got several tokens ("POL" / "99281" / "A"). | Same-type detections separated only by `- / . : #` are merged. | `reconciliation.fragment_joiners` |
| The relation or role was ignored when typing (emergency contact → NAME; employer → FACILITY_NAME). | Cue rules: a relation cue gives RELATIVE_NAME (unless a title precedes the name); an employer cue gives EMPLOYER. | `contextual_type_rules` |
| The model typed patients as providers. This is a **privacy** issue: the guard blocks NAME but only flags PROVIDER_NAME. | A model-only PROVIDER_NAME with no provider cue (title, role, "seen by"…) within 40 characters becomes NAME. | `contextual_type_rules.provider_requires_role` |
| A partial name got a new token ("Laura's" ≠ "Laura Martinez"). | Within the scope, a partial mention reuses the token of the **one** earlier full name containing its words. Ambiguous partials get a new token. | `redaction.name_coreference` |
| Existing false positives found during validation. | "plate count 250000" is not a plate: plates mix letters and digits, and the cue must be close. "IP address:" is not a postal-address field. "into account" / "on account" are not account labels. Labelled values need at least 5 alphanumeric characters. | `patterns.yaml`, `rules`, `labelled.yaml` |

### Evaluation additions

- **Strict typed evaluator.** `python -m harness_eval.strict_eval` scores correct, wrong-type, partial and missing
  entities, plus false positives (honorifics and possessives are neutral).
- **CheckList type checks.** The CheckList now reports **type accuracy**. Section keys may name a variant
  (`DEVICE_ID@mac`), and a section may opt out of specific invariance tests with a documented reason (`skip_inv`).
- **New canary:** `device_location_account`.

### Results

| measure | before | after |
|---|---|---|
| Strict review of the 10 cases (dev): correct / wrong-type / partial / missing / FP | 272 / 111 / 11 / 5 / 7 | **397 / 2 / 0 / 0 / 1** |
| Strict F1 / PHI fully hidden / character coverage | 68.0% / 96.0% / 97.9% | **99.4% / 100% / 100%** |
| CheckList fast tier (cases, type accuracy) | 1319, type not measured | **1966/1966, type 100%** |
| CheckList model tier | 62/62, type 49%* | **75/75, type 100%** |
| `cadence_dev_text`: residual / high-risk recall / strict recall / precision / spurious | 5.83% / 92.2% / 81.95% / 90.65% / 83 | 5.83% / 92.2% / **84.15% / 91.06% / 79** |
| `cadence_dev_asr`: residual / high-risk recall / precision / spurious | 19.97% / 78.9% / 85.88% / 73 | 19.97% / 78.9% / **86.55% / 69** |
| Prompt suite regressions (fast / model) | n/a | **0 / 0** (fast: +3 VEHICLE_ID protected) |
| Redaction errors and must-not-redact violations (dev) | 0 / 0 | 0 / 0 |

\* Measured with the new type check on the same templates.

### Known limitations (deliberately not "fixed")

The model mislabels some organisations and places: an insurer as FACILITY_NAME, a bare city as ADDRESS, "Toyota Camry"
as ADDRESS. None of these has a reliable general signal; all are fully redacted. Dotted version numbers still produce
model fragments ("2.4.1.7").

## 2026-10-07 (later): detection v3 (general fixes from the held-out batch-2 review)

**Process.** Batch 2 (`user_cases_v2`) was scored **once, before any change**, as held-out test data: strict F1 88.4%,
PHI protection 99.6%. It was then used to develop the fixes below, so it was re-roled to **dev** (manifest history
records this). A third, independently written batch is required for the next unbiased measurement.

| Root cause | General fix | Where |
|---|---|---|
| Licence labels were a closed list of professions; "certificate" and "certification" needed "number". | Any licence, certificate, certification, credential or permit noun is a label, whatever precedes it, never "license plate". The qualifiers number, no, #, ID, identifier, reference and ref all apply. | `labelled.yaml` |
| "identifier" was not a qualifier; slash compounds were unsupported. | Health-plan, insurance, insurer, payer, plan, member, beneficiary and subscriber nouns combine with any of these qualifiers. A label binds at its last noun phrase. | `labelled.yaml` |
| "Surname, MD" was read as "City, Maryland". | MD, PA and DC are states only in address position and never directly after a given name or initial. The credential-provider rule accepts middle initials, a wrapped name, and DPT, PharmD, DNP and PhD. It is skipped after locative prepositions ("moved to Silver Spring, MD"). | `rules.py` |
| Names split at an initial or abbreviation ("Kevin L." + "Harper", "St." + "Vincent"). | Same-type groups separated by "&lt;initial or abbreviation&gt;. " are joined during reconciliation, *before* typing. | `reconciliation._join_initials`, `abbreviation_joiners` |
| A vehicle make or model was read as "City, State"; model years were tagged DATE. | `non_phi_phrases.vehicle_make_model` uses a public manufacturer list. It requires a model year or model token *and* a vehicle cue within 40 characters, so "Dr. Henry Ford" stays a name. VEHICLE_ID evidence is never rejected by it. | `exclusions.yaml`, `policy.annotate` |
| Bare years were tagged DATE. | `non_phi_patterns.DATE` rejects a bare year, **on typed text only**. On speech-to-text a garbled date can look like a year ("the eighteenth" was transcribed "2018"; caught on dev ASR), so it stays redacted there. | `exclusions.yaml` (`non_phi_pattern_modalities`) |
| "Michigan registration EPK-7391" was not recognised as a plate. | "&lt;state&gt; (vehicle) registration &lt;plate&gt;" is VEHICLE_ID. "vehicle registration" and "registration plate" were added as labels. Bare "registration" is not a label (DEA registration). | `rules.py`, `labelled.yaml` |
| Some USPS street suffixes were missing (Plaza, Square, Crossing…). | Common USPS Publication 28 suffixes were added. "Center" is excluded because it names facilities. | `rules.py` |
| The provider-cue window reached another person's "Dr." | Context windows stop at the neighbouring detection. "specialist" was removed from the provider cues (as in "billing specialist"). | `policy._context_type` |
| A model subtype guess overrode an explicit field label ("insurer X" → FACILITY_NAME). | A model-only subtype cannot refine a type set by a field label. | `policy.decide` |
| Insurer and payer names were missed. | `labelled_payer_names`: an insurer, insurance carrier, health plan or payer noun followed by a capitalised name gives ORGANIZATION. | `labelled.yaml` |
| The strict scorer credited mixed-type spans by majority. | Every hidden character must carry an accepted type ("Daniel R." PROVIDER + "Mercer" CITY counts as wrong). | `strict_eval.py` |

### Results

| measure | v7 baseline | detection v2 | **detection v3** |
|---|---|---|---|
| batch 1, strict F1 / protected | 68.0% / 96.0% | 99.4% / 100% | **99.5% / 100%** |
| batch 2, strict F1 / protected | (88.4% / 99.6% held-out with v2) | – | **99.8% / 100%** (dev now) |
| CheckList fast (cases, type accuracy) | 1319, not measured | 1966, 100% | **2227/2227, 100%** |
| CheckList model | 62/62 | 75/75, type 100% | **75/75, type 100%** |
| `cadence_dev_text` strict recall / precision / spurious | 81.95% / 90.65% / 83 | 84.15% / 91.06% / 79 | **84.88% / 91.42% / 75** |
| `cadence_dev_asr` precision / spurious | 85.88% / 73 | 86.55% / 69 | **86.47% / 69** |
| dev recall, residual PHI, redaction errors | – | unchanged | **unchanged** (92.2% / 78.9% high-risk; 0 errors) |
| prompt suite regressions (fast / model) | – | 0 / 0 | **0 / 0** |

### Remaining known limitations

These are model labelling errors with no reliable general signal. Every one of them is still fully redacted.

- An insurer named without a payer cue ("under Blue Cross") is typed as a facility.
- A bare city repeated later in the text is typed as an address.
- A heading word ("Telehealth") is typed as a facility.

Distinguishing marks (scars, tattoos, birthmarks; Safe Harbor "(R) other unique identifying characteristic") have no
entity type and are not redacted. **Policy decision needed** on whether to add one.
