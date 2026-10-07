"""Cadence-style templates (v2), split into DISJOINT dev and test template families.

No template appears in both splits (enforced by tests/test_datagen.py). Dev families are
used only for threshold calibration and for developing the deterministic rules; test
families are evaluated once. Text outside placeholders must not contain PHI - every
identifier goes through a slot so it is annotated.

Each entry: (template_id, template, phenomena_tags).
"""
from __future__ import annotations

T = tuple[str, str, list[str]]

TEMPLATES: dict[str, dict[str, list[T]]] = {
    "CLEAN_CLINICAL": {
        "dev": [
            ("cc_d1", "Patient {patient}, DOB {dob_slash}, MRN {mrn}, was seen at {facility} on {date} by {provider}. {patient_titled} is a {age} with type 2 diabetes. Follow up with {provider_short} in 3 months. Contact: {phone}.", []),
            ("cc_d2", "Consult note: {age} referred by {provider2} for chest pain. Records requested from {facility2}. Patient {patient} reports exertional dyspnea. Insurance member number {member_id}. Echo scheduled for {date_slash}.", []),
            ("cc_d3", "Progress note {date_short}. {patient} (DOB {dob}) presents for COPD follow-up. Pharmacy: {org}. Email on file: {email}. Account {account}.", []),
            ("cc_d4", "Referral: {patient} referred to {facility} by {provider_untitled}. Prefers {language}; interpreter needed. Works at {employer}.", ["contextual"]),
        ],
        "test": [
            ("cc_t1", "DISCHARGE SUMMARY\nPatient: {patient_lastfirst}\nMRN: {mrn}\nAdmission date: {date_slash}\nAttending: {provider}\nFacility: {facility}\n\nHospital course: Admitted with community-acquired pneumonia, treated with IV ceftriaxone. Discharged home to {address}, {city}, {state_abbr} {zip}. Daughter {relative_full} will assist with medications.", ["structured"]),
            ("cc_t2", "EXAM: Chest X-ray, two views. DATE: {date_slash}. PATIENT: {patient}. ACCOUNT NUMBER: {account}. ORDERING PHYSICIAN: {provider}. IMPRESSION: No acute process. Results called to {provider_short} at {phone_dashed}.", ["structured"]),
            ("cc_t3", "Medication reconciliation for {patient}, medical record number {mrn}, completed {date}. Member ID {member_alnum}. Prescriber NPI {npi}. Claim {claim}. No changes to lisinopril 10 mg daily.", []),
            ("cc_t4", "{patient} is a {age} with heart failure, EF 35%. Lives with her {relation} {relative} in {city}, {state}. Last echo {date_month_day}. Cardiologist {provider} at {facility}.", ["relatives"]),
            ("cc_t5", "History and physical. {patient_titled} was born {dob_ordinal} and is followed at {facility_short} by {provider_bare}. Allergies: penicillin. Portal: {url}.", ["contextual"]),
            ("cc_t6", "Nursing note: {patient} ambulated with walker. Son {relative} visited and asked that updates go to {email}. Next PT session {rel_next}.", ["relatives"]),
        ],
    },
    "NOISY_ASR": {
        "dev": [
            ("asr_d1", "uh yeah {patient} um she said her appointment with {provider_short_nodot} is {rel_next} and her member number is {member_spoken}", ["asr"]),
            ("asr_d2", "{patient_first}'s uh lives at {address}, you know, and her phone is {phone_spoken}", ["asr"]),
            ("asr_d3", "okay so this is uh {care_team} calling for {patient} um date of birth {dob_spoken} she she wanted to ask about the the refill", ["asr"]),
            ("asr_d4", "medical record number {mrn_spoken} um patient {patient} was seen {rel_last} for for a fall", ["asr"]),
        ],
        "test": [
            ("asr_t1", "yeah so um MRN {mrn_spoken} uh that's for {patient_accent} and she she's at {address_spoken}", ["asr"]),
            ("asr_t2", "he said that {provider_accent} over at {facility_short} told him to stop the the water pill and call back at {phone_spoken_grouped}", ["asr", "provider_facility_ambiguity"]),
            ("asr_t3", "her email is uh {email_spoken} and she she still works at {employer} part time", ["asr", "employer"]),
            ("asr_t4", "so {relative} her {relation} is gonna drive her to {facility} on {date_spoken} for the the scan", ["asr", "relatives"]),
            ("asr_t5", "member number is {member_spoken} uh no wait let me let me check it's for {patient_nick}", ["asr"]),
            ("asr_t6", "um she was born {dob_spoken} and uh the zip is {zip_spoken} near {landmark}", ["asr"]),
        ],
    },
    "TELEPHONE_CONVERSATION": {
        "dev": [
            ("tel_d1", "Agent: Thanks for calling. Can I get your member ID?\nCaller: Sure, it's {member_id}.\nAgent: And the best callback number?\nCaller: {phone}.\nAgent: Your appointment at {facility} is on {date}.", ["conversation"]),
            ("tel_d2", "Coordinator: I'm calling to verify your address.\nMember: I moved. It's {address}, {city}, {state} {zip}.\nCoordinator: And the billing account number is {account}?\nMember: Yes.", ["conversation"]),
            ("tel_d3", "Care Manager: {patient_first}, how did the visit with {provider_bare} go?\nMember: Fine. {provider_bare} wants labs before {rel_next}.\nCare Manager: Your MRN is {mrn}, correct?", ["conversation", "contextual"]),
        ],
        "test": [
            ("tel_t1", "Care Manager: Hi, this is {care_team} from {org}. Am I speaking with {patient}?\nMember: Yes, this is {patient_first}.\nCare Manager: Can you confirm your date of birth?\nMember: It's {dob}.\nCare Manager: I see you saw {provider_short} {rel_last}.\nMember: Yes. My {relation} {relative} has been helping me.", ["conversation", "relatives"]),
            ("tel_t2", "Nurse: Is this {patient_titled}?\nMember: Yes.\nNurse: Following up after your discharge from {facility}. Did you fill the prescription from {provider}?\nMember: My {relation} {relative} picked it up at {org}.\nNurse: We'll call you back at {phone_dashed}.", ["conversation", "relatives"]),
            ("tel_t3", "Member: yeah um my number changed it's {phone_spoken}\nCare Manager: okay and you still see {provider_short_nodot}\nMember: yes over at {facility_short}", ["conversation", "asr"]),
            ("tel_t4", "Interpreter: The member says she prefers {language}.\nCare Manager: {patient}, do you still work at {employer}?\nMember: Only part time now.", ["conversation", "employer"]),
            ("tel_t5", "Caller: Hi, I'm {relative_full}, calling for my mom.\nAgent: What's her member number?\nCaller: Insurance member number {member_alnum}.\nAgent: And her date of birth?\nCaller: {dob_slash}.", ["conversation", "relatives"]),
            ("tel_t6", "Care Manager: Her daughter said she can be reached at {phone}.\nSupervisor: Did you speak with {care_team_first} from the clinic?\nCare Manager: Yes, {yesterday}.", ["conversation", "contextual"]),
        ],
    },
    "FACE_TO_FACE_DICTATION": {
        "dev": [
            ("f2f_d1", "This is {provider} dictating a home visit note for {patient}, date of birth {dob_slash}, member ID {member_id}. Visit date {date}. Seen at home at {address} in {city}. Her {relation} {relative} was present. End of dictation.", ["dictation", "relatives"]),
            ("f2f_d2", "Face to face encounter with {patient_titled} on {date_short}. Accompanied by care coordinator {care_team}. {provider_bare} adjusted her Lasix. New phone number {phone_dots}.", ["dictation", "contextual"]),
            ("f2f_d3", "dictation for {patient_first} um the member was seen {rel_last} at {facility_short} uh plan is to call {care_team_first} with the results", ["dictation", "asr", "contextual"]),
        ],
        "test": [
            ("f2f_t1", "dictating for patient {patient} m r n {mrn_spoken} seen today period she reports dizziness period I will call {provider2} at {facility} comma recheck in two weeks period", ["dictation", "asr"]),
            ("f2f_t2", "Dictation: {patient}, {age_plain}, seen at {facility}. Chief complaint knee pain. Referred by {provider_untitled}, NPI {npi}. Return visit {rel_this}.", ["dictation"]),
            ("f2f_t3", "Okay, starting the note. Member {patient}, D-O-B {dob_ordinal}. Lives alone near {landmark}. Granddaughter {relative_full} calls daily. Next appointment {date_abbr_ord} with {provider_short}.", ["dictation", "relatives"]),
            ("f2f_t4", "New paragraph. Mister {patient_last} states his {relation} {relative} manages the pill box. Record number {mrn}. Follow up {weeks_ago} was missed. End.", ["dictation", "contextual"]),
            ("f2f_t5", "this is {provider_untitled} uh home visit for {patient_nick} um phone {phone_digits} and she she lives at {address_abbr}", ["dictation", "asr"]),
        ],
    },
    "CARE_PLAN": {
        "dev": [
            ("cp_d1", "Care Plan for {patient} (Member ID {member_id})\nGoal 1: A1c below 7% by {date}.\nIntervention: Weekly calls by {care_team}.\nBarrier: Transportation; {relation} {relative} will drive.", ["care_plan", "relatives"]),
            ("cp_d2", "Suggested goal (conversation on {date_slash}): {patient_first} wants to walk to {landmark} three times a week. Review date: {rel_next}.", ["goal_suggestion", "contextual"]),
            ("cp_d3", "Problem: Medication adherence. {org} will deliver to {address_abbr}, {city}. Next review {date_short}.", ["care_plan"]),
        ],
        "test": [
            ("cp_t1", "Care plan update: Member {patient}, DOB {dob}. Primary language {language}. PCP {provider_short}, {facility}. Emergency contact {relative_full} at {phone}.", ["care_plan"]),
            ("cp_t2", "Goal: Reduce ER visits. Two ER visits to {facility} since {date_month_day}. Plan: enroll with {org}. Member works nights at {employer}; call after 2 pm.", ["care_plan", "employer"]),
            ("cp_t3", "Goal suggestions from call with {patient}: 1) Eye exam with {provider2}. 2) Update address to {address}, {zip}. 3) Confirm account {account_dashed} for copay assistance.", ["goal_suggestion"]),
            ("cp_t4", "Barrier identified: {patient_titled} cannot get to {facility_short} {rel_next} because her {relation} {relative} works then. Plan: rideshare via member number {member_alnum}.", ["care_plan", "relatives", "contextual"]),
            ("cp_t5", "Goal: Attend dialysis three times weekly at {facility2}. Owner: {care_team}. Target date {date_spoken_year}.", ["care_plan"]),
        ],
    },
    "MEMBER_SUMMARY": {
        "dev": [
            ("ms_d1", "Member Summary\nName: {patient}\nDOB: {dob_slash}\nMember ID: {member_id}\nMRN: {mrn}\nPCP: {provider} ({facility})\nPhone: {phone}\nAddress: {address}, {city}, {state_abbr} {zip}", ["structured"]),
            ("ms_d2", "{patient} is a {age} member with COPD followed by {provider_short} at {facility}. Lives with her {relation} {relative} in {city}, {state}. Preferred language: {language}.", ["relatives"]),
            ("ms_d3", "Condition summary for member {member_id}: {patient_first} has poorly controlled asthma. Last seen by {provider2_untitled} on {date_slash}.", []),
        ],
        "test": [
            ("ms_t1", "Summary: {patient_lastfirst} | MRN {mrn} | DOB {dob_short} | Acct {account} | PCP NPI {npi} | Contact {phone_digits} | Email {email}", ["structured"]),
            ("ms_t2", "Member {patient} (insurance ID {member_alnum}) engaged since {date_month_day}. Care team: {care_team}. Concerns: depression, food insecurity. Lives near {landmark}.", ["contextual"]),
            ("ms_t3", "{patient_initial} is followed by {provider_untitled} at {facility2}. Coverage through employer {employer} via {org}. Born {dob}.", ["employer", "partial_name"]),
            ("ms_t4", "MEMBER: {patient} DOB: {dob_slash} SUBSCRIBER ID: {member_id} PCP: {provider} FACILITY: {facility} PHONE: {phone_intl}", ["structured"]),
            ("ms_t5", "Social context: {patient_first} lives with {relation} {relative} on {street_full_only}. Attends church near {landmark}. Speaks {language}.", ["contextual", "relatives"]),
        ],
    },
    "TIMELINE": {
        "dev": [
            ("tl_d1", "Timeline for {patient}:\n- {date_slash}: Admitted to {facility} for CHF.\n- {date_short}: Discharged; follow-up with {provider_short}.\n- {rel_last}: Outreach by {care_team}.", ["timeline"]),
            ("tl_d2", "On {date}, {patient_first} called to report leg swelling. Two days later {provider} increased furosemide.", ["timeline"]),
            ("tl_d3", "Event history for MRN {mrn}: born {dob}; diabetes diagnosed in 2015; enrolled with {org} on {date}.", ["timeline"]),
        ],
        "test": [
            ("tl_t1", "In {date_month_day} the member moved to {address}, {city}. Since then {relative_full}, her {relation}, has managed appointments. Member ID {member_id}.", ["timeline", "relatives"]),
            ("tl_t2", "{date_slash} - ER visit at {facility}. {date_short} - PCP visit with {provider_untitled}. Claim {claim} submitted.", ["timeline"]),
            ("tl_t3", "Timeline narration: {patient} fell {weeks_ago}, was seen at {facility_short} {yesterday}, and sees {provider_short} {tomorrow}.", ["timeline", "relative_date"]),
            ("tl_t4", "{date_abbr_ord}: {patient_titled} started dialysis at {facility2}. {date_md}: missed session. {rel_this}: transport arranged by {care_team}.", ["timeline"]),
            ("tl_t5", "Her birthday was {rel_last}; she turned {age_plain}. On {date_spoken_year} she was readmitted to {facility}.", ["timeline", "relative_date"]),
        ],
    },
    "CARE_ASSISTANT": {
        "dev": [
            ("ca_d1", "Question: When is {patient}'s next appointment with {provider_short}?\nRetrieved context: [Member: {patient} | DOB: {dob_slash} | Member ID: {member_id} | PCP: {provider} | Facility: {facility} | Next appt: {date}]", ["rag_context"]),
            ("ca_d2", "User: Summarize the last call with {patient_first}.\nContext: Call on {date_short} with {care_team}. Member reported missed doses. Callback {phone}.", ["rag_context"]),
            ("ca_d3", "Spoke with {patient_first} today. She said {relative} will bring her to the clinic.", ["contextual", "relatives"]),
        ],
        "test": [
            ("ca_t1", "Q: Does member {member_id} have a ride to {facility} {rel_next}?\nContext: Member lives at {address}, {city}. Her {relation} {relative} usually drives.", ["rag_context", "relatives"]),
            ("ca_t2", "Can you draft a goal for {patient_titled} based on the visit with {provider_bare} at {facility_short}?", ["contextual", "provider_facility_ambiguity"]),
            ("ca_t3", "What did {provider} prescribe for medical record number {mrn}? Also check account {account} for prior auth.", []),
            ("ca_t4", "I spoke with {provider_bare} at {facility_short}. Later {provider_short} called from {facility}.", ["contextual", "provider_facility_ambiguity"]),
            ("ca_t5", "She works at {employer} on {street_full_only}. Her email is {email} and she prefers {language}.", ["contextual", "employer"]),
            ("ca_t6", "Retrieved transcript excerpt: \"...yeah my {relation} {relative} lives with me now, she moved from {city}...\" Question: who lives with {patient_first}?", ["rag_context", "relatives"]),
        ],
    },
    "CONTEXTUAL_PHI": {
        "dev": [
            ("ctx_d1", "The member lives with her {relation} {relative}.", ["contextual", "relatives"]),
            ("ctx_d2", "She sees {provider_short} at {facility_short}.", ["contextual"]),
            ("ctx_d3", "The care manager spoke with {care_team_first} from the clinic.", ["contextual"]),
            ("ctx_d4", "{patient_first} called back; {relative} answered and said {patient_nick} was asleep.", ["contextual", "relatives"]),
        ],
        "test": [
            ("ctx_t1", "Her {relation} {relative} usually takes her to appointments.", ["contextual", "relatives"]),
            ("ctx_t2", "The patient works at {employer} on {street_full_only}.", ["contextual", "employer"]),
            ("ctx_t3", "Her next appointment is {weekday}.", ["contextual", "relative_date"]),
            ("ctx_t4", "She lives near the intersection of {street_full_only} and {street2_only}.", ["contextual", "intersection"]),
            ("ctx_t5", "Her {relation} said she can be reached at {phone}.", ["contextual", "relatives"]),
            ("ctx_t6", "{patient_titled} says {provider_bare} is the only doctor she trusts at {facility_short}.", ["contextual", "provider_facility_ambiguity"]),
            ("ctx_t7", "Left voicemail for {patient_nick}. {relative} called back from {employer} and asked us to try {tomorrow}.", ["contextual", "relatives", "nickname"]),
            ("ctx_t8", "{patient_initial} reports that her neighbor {relative_full} checks on her near {landmark}.", ["contextual", "partial_name"]),
        ],
    },
    "SPANISH": {
        "dev": [
            ("es_d1", "{patient} vive en {address_es}, {zip_es} {city_es}. El número de afiliado es {member_id}. La cita con {provider_es} es el {date_es}.", ["spanish"]),
            ("es_d2", "Llamada con la {patient_titled_es}: su teléfono es {phone_es} y su correo es {email}. Prefiere hablar {language_es}.", ["spanish"]),
            ("es_d3", "eh sí {patient_first} dice que su número es {phone_spoken_es} y que vive en {address_es}", ["spanish", "asr"]),
        ],
        "test": [
            ("es_t1", "Paciente {patient}, fecha de nacimiento {dob_es}, número de historia clínica {nhc_es}. Atendida en {facility} por {provider_es}. Su {relation_es} {relative} la acompaña.", ["spanish", "relatives"]),
            ("es_t2", "La señora {patient} tiene {age_es} y trabaja en {employer}. Vive en {address_es}, {city_es}, código postal {zip_es}.", ["spanish", "employer"]),
            ("es_t3", "Coordinadora {care_team}: confirmé la cita del {date_es_short} con {provider_es}. La paciente llamará {rel_es}.", ["spanish"]),
            ("es_t4", "Resumen: {patient}, DNI {dni}, cuenta {account}, nacida el {dob_es_slash}. Teléfono {phone_es_local}.", ["spanish"]),
            ("es_t5", "Su {relation_es} {relative} dice que la paciente fue ingresada en {facility_short} {ayer} y que su correo es {email_spoken_es}.", ["spanish", "asr", "relatives"]),
            ("es_t6", "El paciente vino de {country} hace años. Cita el {date_es_spoken} con {provider_es} en {facility}.", ["spanish"]),
        ],
    },
    "MIXED_ENGLISH_SPANISH": {
        "dev": [
            ("mx_d1", "{patient_first} dijo que vive en {street_full_only} y que su appointment is {rel_next}.", ["code_switching"]),
            ("mx_d2", "Okay, so {patient} me dijo que su member ID es {member_id} and her phone is {phone}.", ["code_switching"]),
            ("mx_d3", "The member said que su hija {relative} la va a llevar a {facility} on {date}.", ["code_switching", "relatives"]),
        ],
        "test": [
            ("mx_t1", "{provider_es} wants her to come back el {date_es_short}, pero she needs a ride from {address}.", ["code_switching"]),
            ("mx_t2", "Her daughter dice que {patient_first} ya no trabaja en {employer}, she prefers {language_es}.", ["code_switching", "employer"]),
            ("mx_t3", "Llamé a {provider_short} y me dijo que the labs from {facility_short} are normal. MRN {mrn}.", ["code_switching"]),
            ("mx_t4", "Sí, mi número es {phone_spoken_es}, okay, and my email is {email}.", ["code_switching", "asr"]),
            ("mx_t5", "My {relation} {relative} va a llamar {tomorrow} para confirmar la cita at {facility2}.", ["code_switching", "relatives"]),
        ],
    },
    "HARD_IDENTIFIERS": {
        "dev": [
            ("hid_d1", "MRN: {mrn}. Also listed as record number {mrn}.", ["hard_id"]),
            ("hid_d2", "The medical record number is {mrn_spoken}.", ["hard_id", "asr"]),
            ("hid_d3", "Member ID {member_id}; insurance member number {member_alnum}.", ["hard_id"]),
            ("hid_d4", "Call {phone_digits} or {phone_spoken_grouped}.", ["hard_id", "asr"]),
            ("hid_d5", "Legacy export: {member_stylized} / {mrn_stylized}.", ["hard_id", "stylized"]),
        ],
        "test": [
            ("hid_t1", "MRN {mrn}; alternate record {mrn_colon}; chart {mrn_hash}.", ["hard_id"]),
            ("hid_t2", "member number is {member_id}. Member number {member_spoken} was read back.", ["hard_id", "asr"]),
            ("hid_t3", "provider NPI {npi}. Referring NPI read aloud as {npi_spoken}.", ["hard_id", "asr"]),
            ("hid_t4", "Phone {phone_dots}, alt {phone_intl}, cell {phone_spoken}.", ["hard_id", "asr"]),
            ("hid_t5", "Billing account number {account_dashed}; subscriber ID {member_alnum}; claim number {claim}.", ["hard_id"]),
            ("hid_t6", "Email {email}, portal {url}.", ["hard_id"]),
            ("hid_t7", "MRN {mrn_spoken}. Repeat back, the record number is {mrn}.", ["hard_id", "asr"]),
            ("hid_t8", "Lives at {address}, also recorded as {street_only}, in {city}, {state_abbr} {zip}.", ["hard_id"]),
            ("hid_t9", "Legacy IDs: {member_stylized}, {mrn_stylized}.", ["hard_id", "stylized"]),
        ],
    },
    "DATE_FORMS": {
        "dev": [
            ("dt_d1", "DOB {dob_slash}; also written {dob_short}; the member says {dob_ordinal}.", ["date_forms"]),
            ("dt_d2", "Seen {date_slash}, again {date_month_day}, and next {rel_next}.", ["date_forms"]),
            ("dt_d3", "She was born {dob_spoken} and was last seen {weeks_ago}.", ["date_forms", "asr"]),
        ],
        "test": [
            ("dt_t1", "Date of birth {dob}. Seen {date_abbr_ord} and {date_day_month}.", ["date_forms"]),
            ("dt_t2", "DOB {dob_spoken}. Appointment {date_spoken_year}.", ["date_forms", "asr"]),
            ("dt_t3", "Last visit {rel_last}, next visit {rel_next}, labs {rel_this}.", ["date_forms", "relative_date"]),
            ("dt_t4", "She called {yesterday} and will come in {tomorrow}; symptoms began {weeks_ago}.", ["date_forms", "relative_date"]),
            ("dt_t5", "Her birthday was {rel_last}. Follow-up on {date_short}, not {date_malformed}.", ["date_forms", "relative_date"]),
            ("dt_t6", "Admitted {date_slash}, discharged {date_md}, call scheduled {weekday}.", ["date_forms"]),
        ],
    },
    "NEGATIVE_CONTROL": {
        "dev": [
            ("neg_d1", "The clinic is open Monday through Friday. BP 120/80, HR 72. Take 1/2 tablet of metoprolol twice daily.", ["negative_control"]),
            ("neg_d2", "History of Parkinson disease and Bell palsy. Medicare and Medicaid information reviewed. Follow up in 3 months.", ["negative_control"]),
            ("neg_d3", "Glasgow Coma Scale 15. A1c 7.2%. Room 4B. Type 2 diabetes. Tylenol 500 mg q6h PRN.", ["negative_control"]),
        ],
        "test": [
            ("neg_t1", "Smith fracture of the left wrist, cast placed. Colles fracture ruled out. Foley catheter removed.", ["negative_control"]),
            ("neg_t2", "Lyme disease serology negative. West Nile virus screening not indicated. Rocky Mountain spotted fever considered.", ["negative_control"]),
            ("neg_t3", "The member was born in the United States. She attends the senior center on {weekday_generic}s. ICU stay 2 weeks.", ["negative_control"]),
            ("neg_t4", "Down syndrome, Hodgkin lymphoma in remission, Apgar scores 8 and 9. Dr. visit due in 6 months.", ["negative_control"]),
            ("neg_t5", "Pharmacy hours are 9 to 5, Monday to Saturday. Dose 2/3 of previous. Weight 160 lbs, height 5'4\".", ["negative_control"]),
            ("neg_t6", "Reviewed Medicare Part D formulary. PCP visit in the ER was not needed. Cardiology at the main campus.", ["negative_control"]),
        ],
    },
}

SUBSET_NOISE: dict[str, list[tuple[str, float]]] = {
    "CLEAN_CLINICAL": [("clean", 1.0)],
    "NOISY_ASR": [("medium", 0.5), ("high", 0.5)],
    "TELEPHONE_CONVERSATION": [("clean", 0.3), ("low", 0.3), ("medium", 0.25), ("high", 0.15)],
    "FACE_TO_FACE_DICTATION": [("low", 0.4), ("medium", 0.4), ("high", 0.2)],
    "CARE_PLAN": [("clean", 0.6), ("low", 0.4)],
    "MEMBER_SUMMARY": [("clean", 0.7), ("low", 0.3)],
    "TIMELINE": [("clean", 0.7), ("low", 0.3)],
    "CARE_ASSISTANT": [("clean", 0.5), ("low", 0.5)],
    "CONTEXTUAL_PHI": [("clean", 0.5), ("low", 0.3), ("medium", 0.2)],
    "SPANISH": [("clean", 0.5), ("low", 0.25), ("medium", 0.25)],
    "MIXED_ENGLISH_SPANISH": [("low", 0.5), ("medium", 0.5)],
    "HARD_IDENTIFIERS": [("clean", 0.5), ("low", 0.3), ("medium", 0.2)],
    "DATE_FORMS": [("clean", 0.6), ("low", 0.2), ("medium", 0.2)],
    "NEGATIVE_CONTROL": [("clean", 0.7), ("low", 0.3)],
    "LONG_DOCUMENTS": [("clean", 0.5), ("low", 0.5)],
}
SUBSET_LANGUAGE = {"SPANISH": "es", "MIXED_ENGLISH_SPANISH": "mixed"}

# ---- long documents ------------------------------------------------------------------
LONG_FILLER: list[str] = [
    "The patient tolerated the visit well and denied chest pain, shortness of breath, or palpitations.",
    "Vital signs were within normal limits apart from mildly elevated blood pressure.",
    "Medication list was reviewed line by line and no discrepancies were identified.",
    "We discussed a low-sodium diet and daily weight monitoring.",
    "The member reports adequate sleep but occasional nocturia two to three times nightly.",
    "Home safety assessment showed no loose rugs and adequate lighting in the hallway.",
    "Laboratory results showed stable renal function and potassium of 4.2.",
    "Depression screening was repeated and the score improved compared with the prior quarter.",
    "Education was provided on recognizing early signs of hypoglycemia.",
    "The member continues to use a walker for ambulation outside the home.",
    "No new allergies were reported and immunizations are up to date per the member.",
    "Transportation barriers were discussed and community resources were offered.",
    "The care team will continue monthly outreach and reassess goals at the next review.",
    "Physical exam revealed trace bilateral ankle edema without calf tenderness.",
    "The member verbalized understanding of the plan and agreed to call with any concerns.",
]
LONG_PHI_DEV: list[str] = [
    "Member {patient} was contacted by {care_team} on {date}.",
    "Follow-up was arranged with {provider} at {facility}.",
    "The member confirmed her phone number as {phone} and member ID {member_id}.",
]
LONG_PHI_TEST: list[str] = [
    "Her {relation} {relative} attended and lives nearby at {address}, {city}.",
    "Records were sent to {provider_short}; MRN {mrn}.",
    "Date of birth was verified as {dob_slash} before discussing results.",
    "Billing account {account} was updated and a copy was e-mailed to {email}.",
    "The member works at {employer} and prefers communication in {language}.",
    "Follow-up with {provider} at {facility} is set for {date}.",
    "Callback number confirmed as {phone}; member ID {member_alnum}.",
]
LONG_SIZES_TOKENS: list[int] = [500, 1000, 2000, 4000, 8000, 12000, 16000]
