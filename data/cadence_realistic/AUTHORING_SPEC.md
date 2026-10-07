# Authoring spec: Cadence-realistic PHI evaluation documents (Tier 3)

You are writing realistic, FREE-FORM documents of the kind a care-management company
("Cadence") handles. Every document must read like a real person said or wrote it -
no templates, no repeated sentence frames, varied length (80-450 words), varied tone.
Do not look at any other files in this repository except this spec and the validator.

## Absolute rules
- All people, IDs, addresses, phones and emails are FABRICATED.
- US phones: use the fictional 555-0100..555-0199 range (e.g. 312-555-0147, spoken "three one two, five five five, zero one four seven").
- Emails: only @example.com / @example.org / @example.net.
- Do NOT use real celebrities or real clinicians. Ordinary-sounding names of mixed backgrounds are fine.
- Real organisation names are allowed only as generic context (e.g. "Walgreens", "Medicaid").

## PHI markup (mandatory)
Wrap EVERY PHI span exactly as it appears in the text:  [[LABEL:surface text]]
Labels (use exactly these):
  NAME (the member/patient)  RELATIVE_NAME  PROVIDER_NAME (clinicians)  CARE_TEAM_MEMBER (Cadence staff, office staff)
  FACILITY_NAME  EMPLOYER  ORGANIZATION  ADDRESS (street-level, incl. street name alone or intersections)
  CITY  STATE  ZIP  GEOGRAPHIC_CONTEXT (landmarks, neighbourhoods)  DATE  DOB  PHONE  EMAIL  URL
  MEMBER_ID (member / insurance / Medicaid / subscriber IDs)  MRN  ACCOUNT_ID  NPI  OTHER_UNIQUE_ID  LANGUAGE (preferred language)
Policy (what IS PHI): every person name (incl. first-name-only, last-name-only, nicknames, "Mrs. Lee" -> mark "Mrs. Lee"),
  full or partial dates, DOB, relative dates tied to the person ("next Tuesday", "yesterday", "two weeks ago",
  "her birthday was last Monday"), all identifiers (written, spoken digit-by-digit, partial, or self-corrected),
  street addresses/street names/intersections, city, state, ZIP, landmarks that locate the person, employers,
  facility names (incl. short forms "St. Mary's"), preferred language.
Policy (NOT PHI - do not mark, but DO include such phrases naturally for over-redaction testing):
  ages, years alone, countries, durations in months/years ("for three years"), follow-up intervals ("in 6 weeks"),
  habitual weekdays ("clinic is open Monday through Friday", "dialysis on Tuesdays"), eponyms and diseases
  (Parkinson's, Bell's palsy, Graves disease, Lyme, Down syndrome), drug names, vitals and doses (BP 142/88, 1/2 tab),
  relation words without names ("her daughter"), generic roles ("the nurse", "the pharmacist").
If a speaker self-corrects an ID ("four two, no sorry, four three nine..."), mark the whole spoken span as one entity.

## Realism requirements (mix these across your documents)
member + relative + provider in one conversation; member named indirectly; first-name-only and last-name-only references;
providers and facilities that are NOT in the registry; employers; addresses spoken naturally ("I'm over on Elm, by the
Kroger"); dates spoken naturally ("the fourteenth", "a week from Friday"); phone numbers digit by digit; member IDs / MRNs
spoken, partially, or with corrections; filler words, interruptions, false starts, repeated names, mishearings
("Is that Thompson with a P?"); missing punctuation in some transcripts; apostrophes and possessives.

## Registry (per document)
Also output what Cadence's registries would know BEFORE the document - deliberately INCOMPLETE:
{"member": {"name": "...", "dob": "YYYY-MM-DD", "member_ids": ["..."], "mrn": "...", "phone": "10 digits", "email": "...",
            "address": "1234 Oak Street", "city": "...", "zip": "..."},
 "providers": [{"name": "First Last", "npi": "10 digits"}], "staff": ["First Last"],
 "facilities": [{"name": "...", "aliases": ["..."]}], "employers": [], "contacts": ["First Last"]}
Include only the member's PRIMARY providers/facilities; at least a third of documents must mention a provider or facility
or relative that is NOT in the registry. Registry values should match the canonical form (e.g. full name) even when the
text uses a nickname, a misspelling or a partial form.

## Output format
One JSON object per line (JSONL), UTF-8:
{"id": "<prefix>_<nn>", "doc_type": "call|dictation|care_note|member_summary|timeline|assistant_query",
 "language": "en|es|mixed", "spoken": true|false, "speakers": ["CM", "MEMBER", ...],
 "scenario": "one-line description", "text": "<marked-up text; calls use one line per turn: 'SPEAKER: words'>",
 "registry": {...}}
`spoken: true` documents will later be converted to audio and run through speech recognition, so write calls and
dictations exactly as people SPEAK (numbers as words when spoken digit-by-digit, "doctor" not "Dr.", etc.).
Written documents (care notes, summaries, timelines, assistant queries) use normal written forms.

## Validate before finishing
    .venv/bin/python -m harness.decision.markup <your_file.jsonl>
must print OK. Fix any error it reports.
