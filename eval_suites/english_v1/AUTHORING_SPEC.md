# Prompt suite english_v1 — authoring specification

All content is FABRICATED. Never use real people, real member numbers, real addresses of private
persons, or anything copied from elsewhere. Phones use 555-01xx (e.g. 415-555-0198). Emails use
@example.com / @example.org / @example.net. SSNs use 9xx-xx-xxxx or 000-prefixed invalid ranges only
when the test is about a NON-SSN; for SSN tests use clearly fake values like 123-45-6789 is FORBIDDEN
(too famous) — use e.g. 219-09-9999 style values. NPIs: 10 digits (any). Do NOT read the engine code,
configs, results or tests under harness/, configs/, results/, tests/ — expectations must be written
blind, from the text alone.

## File format: one JSON object per line (JSONL), UTF-8

{"prompt_id": "A-001", "group": "A_single_entity", "modality": "typed",
 "holdout": false,
 "text": "The patient's phone number is 415-555-0198.",
 "registry": null,
 "expected": [
   {"entity_type": "PHONE", "subtype": "PHONE", "match": "415-555-0198", "occurrence": 1,
    "expected_action": "REDACT", "expected_sources": ["DETERMINISTIC"]}
 ],
 "must_not_redact": ["phone number"],
 "asr": null,
 "status": "expected_pass",
 "notes": "short reason / what is being tested"}

Field rules
- prompt_id: <GROUP LETTER>-<3 digits>, unique.
- group: one of A_single_entity, B_multi_entity_clinical, C_conversational, D_asr_style, E_contextual,
  F_registry_scoped, G_edge_case, N_negative.
- modality: typed | note | dictation | telephone | asr.
- holdout: true for every 4th prompt in your file (prompt 4, 8, 12, ...), else false.
- text: the input. 1–6 sentences (multi-entity notes may be longer, max ~120 words).
- registry: null, or {"subject_context_id": "ctx_suite_XXX"} referring to a registry file you write
  (group F only).
- expected: EVERY piece of PHI in the text, in order of appearance. Empty list for negative prompts.
  - entity_type: one of the canonical types below.
  - subtype: fine-grained label from the list below (use the entity_type itself if none fits).
  - match: the exact substring of `text` that is the PHI span (copy it exactly, including case).
    Do NOT include courtesy titles (Dr., Mrs.) or ID keywords ("MRN", "member ID") inside the span.
    Do NOT include leading articles/prepositions ("the", "on", "in").
  - occurrence: 1-based index of which occurrence of `match` in `text` (if the same string appears twice,
    write two entries with occurrence 1 and 2).
  - expected_action: REDACT (all real PHI) — use KEEP only for things that look like PHI but are not.
  - expected_sources: your expectation of which detector family SHOULD catch it, any of
    DETERMINISTIC (regex-like: phone, email, IDs, ZIP, structured dates), CONVERSATIONAL (spoken /
    spelled forms), REGISTRY (only group F, values present in the registry), MODEL (contextual names,
    relatives, facilities, providers, employers, organisations, unusual dates).
- must_not_redact: exact substrings that are NOT PHI and must survive redaction (doses, durations,
  clinical terms, counts, generic roles). Give 1–3 per prompt where natural; required for group N.
- asr: null except group D: {"source_text": "<the clean text the speaker actually said>",
  "lost": [{"entity_type": "...", "source_match": "<PHI in source_text that the ASR text no longer contains>"}]}
  Use "lost" ONLY when the ASR text has destroyed the identifier (e.g. digits dropped or replaced by
  a different word). If the PHI survives in the ASR text (even reformatted), list it in `expected`.
- status: "expected_pass" (always, at authoring time).
- notes: what the prompt tests.

## Canonical entity types and subtypes
NAME (MEMBER_NAME, PARTIAL_NAME, SPELLED_NAME, INITIALS) · RELATIVE_NAME (SPOUSE_NAME, PARENT_NAME,
CHILD_NAME, CAREGIVER_NAME, SIBLING_NAME) · CARE_TEAM_MEMBER (STAFF_NAME) · PROVIDER_NAME (PROVIDER) ·
FACILITY_NAME (HOSPITAL, CLINIC, PHARMACY) · ADDRESS (STREET_ADDRESS, PO_BOX, UNIT, SPOKEN_ADDRESS) ·
CITY · STATE · ZIP (ZIP_CODE, SPOKEN_ZIP) · GEOGRAPHIC_CONTEXT (LANDMARK, NEIGHBORHOOD) ·
DOB (DOB, SPOKEN_DOB) · DATE (ADMISSION_DATE, DISCHARGE_DATE, APPOINTMENT_DATE, PROCEDURE_DATE,
SERVICE_DATE, RELATIVE_DATE, SPOKEN_DATE) · AGE_OVER_89 · PHONE (PHONE, FAX, PARTIAL_PHONE, SPOKEN_PHONE) ·
EMAIL (EMAIL, SPOKEN_EMAIL) · URL · IP_ADDRESS · SSN · MRN (MRN, SPOKEN_MRN) ·
MEMBER_ID (MEMBER_ID, HEALTH_PLAN_ID, POLICY_ID, MBI, SPOKEN_MEMBER_ID) · ACCOUNT_ID ·
LICENSE_CERT_ID (DRIVER_LICENSE, MEDICAL_LICENSE) · VEHICLE_ID (VIN, PLATE) · DEVICE_ID (SERIAL) ·
NPI · OTHER_UNIQUE_ID (CLAIM, CASE, AUTH, ORDER, GATE_CODE) · EMPLOYER · ORGANIZATION (SCHOOL, AGENCY) ·
LANGUAGE (preferred language of the member)

Policy reminders: AGE is PHI only when > 89 (AGE_OVER_89); other ages are NOT PHI (put them in
must_not_redact). Years alone ("in 2019") are not PHI. Habitual weekdays ("dialysis on Mondays") are not
PHI. "today"/"now" are not PHI. Generic roles ("the nurse", "her doctor") are not PHI. The organisation
name "Cadence" (the care-management company) is not member PHI — put it in must_not_redact if used.

## Self-check before finishing (you may run python on YOUR OWN file only)
For each line: valid JSON; every `match` occurs in `text` at least `occurrence` times; prompt_ids
unique; every 4th prompt holdout; group N has empty `expected` and non-empty `must_not_redact`;
group D `asr.lost[].source_match` occurs in `asr.source_text`.
