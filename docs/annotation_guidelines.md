# Gold annotation guidelines (synthetic Cadence benchmark)

Gold spans are produced programmatically by the template renderer (`harness/datagen`),
so offsets are exact by construction and validated on load (`Sample.validate_offsets`).

| Rule | Decision |
| Policy file | `configs/redaction_policy.yaml` (v2) governs what is PHI. |
|---|---|
| Offsets | Character offsets `[start, end)` into the final (post-noise) text. |
| Titles | Provider and patient titles (`Dr.`, `Dra.`, `Mrs.`, `Sra.`, spoken `doctor`) are **inside** the gold span, as in the brief's example, but **excluded from core characters**, so a prediction without the title still counts as full coverage. |
| ID affixes | Literal affix words inside ID values (`MRN`, `TEST`, `MBR`, `NPI`, `ACCT`) are inside the span but excluded from core characters. Digits are always core. |
| Keyword vs value | In `MRN 847291` or `member ID TEST-MBR-938271`, the keyword (`MRN`, `member ID`) is **not** annotated. Only the value is. |
| Possessive | `Maria's` → gold `Maria` (the possessive is excluded). |
| Addresses | Street (`1450 Example Avenue`), city, state and ZIP are separate entities (ADDRESS / CITY / STATE / ZIP). Street-name-only mentions (`Example Ave`) are ADDRESS (tag `partial_address`). |
| Dates | All dates, including partial (`9/18`, `March 18`) and **relative dates** (`next Tuesday`, `last Monday`, `el próximo martes`), are DATE, as the brief requires. Dates of birth are DOB. Tags let you exclude relative dates if your policy differs. |
| Spoken numbers | `five five five four one zero …` is the PHONE / MEMBER_ID / MRN value (tag `spoken_number`). For `test mbr nine three …`, the whole phrase is the MEMBER_ID span; `test` and `mbr` are non-core. |
| People | Patient/member → NAME; family member → RELATIVE_NAME; clinician → PROVIDER_NAME; Cadence care staff (care manager, coordinator) → CARE_TEAM_MEMBER. A bare surname used for a clinician (`Carter`) is PROVIDER_NAME (tag `provider_facility_ambiguity`). |
| Organisations | Hospital/clinic → FACILITY_NAME (short forms like `Sunrise Valley` too); employer → EMPLOYER; health plan, pharmacy, agency → ORGANIZATION. |
| Contextual | Language names → LANGUAGE; landmarks → GEOGRAPHIC_CONTEXT. |
| Ages | Not a canonical category. Ages are **ignore spans**: predictions there are neither TP nor FP. |

Entity `difficulty` (easy/medium/hard) and `tags` come from the slot that generated the entity
(see `harness/datagen/slots.py`). Sample `metadata.phenomena` comes from the template.

## v2 policy additions

| Rule | Decision |
|---|---|
| Relative dates | `next/last/this <weekday>`, an appointment weekday ("her next appointment is Tuesday"), `yesterday`, `tomorrow`, `N days/weeks ago` are DATE. |
| Not PHI | Habitual weekdays ("open Monday through Friday", "on Tuesdays"), durations in months/years, follow-up intervals, years alone, countries (ignore spans), ages (ignore spans), eponyms, clinical values (120/80, 1/2 tablet, 6/10). |
| Identifier formats | Natural formats are primary (`MRN 4829103`, `Member ID 83749201`, `XEH394829103`, 10-digit NPI); stylised `TEST-MBR-` / `MRN-` forms are a tagged minority. |
| Registry context | Each synthetic document carries `metadata.registry`: member record, primary provider, care-team staff, facilities; relatives/employers only sometimes; a second provider/facility never (tag `out_of_registry`). |
| Partial names | `M. Thompson`, `Mrs. Thompson`, nicknames (`Bob` for Robert) are NAME. |

## Real governed data (T4) protocol: required before any release decision

The rules above define *what* is PHI for every dataset. T4 also needs a governed process, because it is the only
evidence that can satisfy gates G1–G3 and G6.

| step | requirement |
|---|---|
| Access | Approved data-use agreement or IRB determination. Annotation runs in the protected environment only. No span text leaves it; evaluation outputs run with `show_text: never`. |
| Sampling | Stratified by modality (typed, note, dictation, telephone, ASR), call type and length bucket. Enough documents for ≥ 300 mentions per direct-identifier category and ≥ 300 documents (G1/G3). Sampled before any detector output is seen. |
| Double annotation | Two trained annotators per document, working independently, without detector pre-annotation. They follow this guide, plus the catalog definitions in `configs/phi/entities.yaml`. |
| Agreement | Span-level F1 and Cohen's κ per entity, reported with the dataset. Below 0.85 F1 for a category, revise the guideline and re-annotate that category. |
| Adjudication | A third reviewer resolves disagreements. Decisions are logged with the rule applied, and new rules are added to this file. |
| Freeze | Register in `configs/eval/datasets.yaml` with `role: release`, `phi_class: real_governed`, a SHA-256 and an access class. From then on: no rule development, threshold tuning or annotation edits based on its results. Every access is ledgered. |
| Retention | Set by the governance agreement. The datasheet (`python -m harness_eval.cards datasheets`) records it. |
