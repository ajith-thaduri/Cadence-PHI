# PHI detection & redaction engine - system card

_Generated 2026-10-07 15:30 by `python -m harness_eval.cards system-card` for environment `prod`. Do not edit by hand - regenerate._

| item | value |
|---|---|
| config sha256 | `3baf6737d98530b5` |
| catalog version | `2026.10.1` |
| code commit | `uncommitted` |
| code fingerprint | `3d225a496346733d` |
| threshold overlays | thresholds_calibrated.yaml |

## Intended use

Detect and redact (tokenize) PHI in English member-call transcripts, care notes and dictations before the text crosses a trust boundary (e.g. an LLM call). Tokens are reversible only through the vault, inside the boundary. Out of scope: languages other than English (requests are rejected), images/PDF, de-identification claims under Expert Determination, any use without the outbound guard in front of external services.

## Pipeline

normalization views -> deterministic rules / declarative patterns / conversational rules / scoped registry / contextual model (evidence only) -> policy thresholds (dev-calibrated) -> reconciliation (non-overlapping, precedence, type refinement) -> redaction (Presidio AnonymizerEngine) -> vault -> outbound guard (independent, no ML). Fail-closed on every failure class (NIST SP 800-53 SC-24 / SI-17).

## Models

| key | enabled | mode | model id | revision | weights sha256 pinned |
|---|---|---|---|---|---|
| obi_roberta | True | production | obi/deid_roberta_i2b2 | `78f2152eb93d` | yes |
| stanford_v2 | False | production | StanfordAIMI/stanford-deidentifier-v2 | `244fba67a648` | no |
| mednotes_gliner | False | production | Lekhansh/modern-gliner-large-mednotes-deidentifier | `222fe2b47dfc` | no |

## Entities (catalog)

| entity | enabled | risk tier | Safe Harbor | default action | outbound guard |
|---|---|---|---|---|---|
| NAME | True | direct | ['names'] | TOKENIZE | block |
| RELATIVE_NAME | True | direct | ['names'] | TOKENIZE | block |
| PROVIDER_NAME | True | contextual | ['names'] | TOKENIZE | flag |
| CARE_TEAM_MEMBER | True | contextual | ['names'] | TOKENIZE | off |
| FACILITY_NAME | True | quasi | ['geographic'] | TOKENIZE | off |
| ADDRESS | True | direct | ['geographic'] | TOKENIZE | block |
| CITY | True | quasi | ['geographic'] | TOKENIZE | off |
| STATE | True | contextual |  | TOKENIZE | off |
| ZIP | True | quasi | ['geographic'] | TOKENIZE | block |
| GEOGRAPHIC_CONTEXT | True | quasi | ['geographic'] | TOKENIZE | off |
| GEO_COORDINATES | True | direct | ['geographic'] | TOKENIZE | block |
| DATE | True | quasi | ['dates'] | TOKENIZE | off |
| DOB | True | direct | ['dates'] | TOKENIZE | block |
| AGE_OVER_89 | True | quasi | ['age_over_89'] | TOKENIZE | off |
| PHONE | True | direct | ['telephone', 'fax'] | TOKENIZE | block |
| EMAIL | True | direct | ['email'] | TOKENIZE | block |
| URL | True | direct | ['url'] | TOKENIZE | block |
| IP_ADDRESS | True | direct | ['ip_address'] | TOKENIZE | off |
| SSN | True | direct | ['ssn'] | TOKENIZE | off |
| MRN | True | direct | ['mrn'] | TOKENIZE | block |
| MEMBER_ID | True | direct | ['health_plan_beneficiary'] | TOKENIZE | block |
| ACCOUNT_ID | True | direct | ['account'] | TOKENIZE | block |
| LICENSE_CERT_ID | True | direct | ['certificate_license'] | TOKENIZE | off |
| VEHICLE_ID | True | direct | ['vehicle'] | TOKENIZE | off |
| DEVICE_ID | True | direct | ['device'] | TOKENIZE | off |
| NPI | True | contextual |  | TOKENIZE | off |
| OTHER_UNIQUE_ID | True | direct | ['other_unique_id'] | TOKENIZE | block |
| EMPLOYER | True | quasi |  | TOKENIZE | off |
| ORGANIZATION | True | contextual |  | TOKENIZE | off |
| LANGUAGE | True | contextual |  | TOKENIZE | off |

## Recognizers

33 of 40 enabled; shadow: none.

| recognizer | enabled | version | developed on |
|---|---|---|---|
| rule_phone | True | 1 | synthetic_dev, meddocan_dev |
| rule_spoken_number | True | 1 | synthetic_dev |
| rule_email_url | True | 1 | synthetic_dev |
| rule_identifier | True | 1 | synthetic_dev, meddocan_dev |
| rule_zip | True | 1 | synthetic_dev, meddocan_dev |
| rule_date | True | 1 | synthetic_dev, meddocan_dev |
| rule_address | True | 1 | synthetic_dev, meddocan_dev |
| rule_titled_name | True | 1 | synthetic_dev |
| conv_spoken_id | True | 1 | cadence_dev |
| conv_partial_phone | True | 1 | cadence_dev |
| conv_ordinal_date | True | 1 | cadence_dev |
| conv_spoken_address | True | 1 | cadence_dev |
| conv_spelled_name | True | 1 | cadence_dev |
| conv_language | True | 1 | cadence_dev |
| registry_member | True | 1 | - |
| registry_provider | True | 1 | - |
| registry_staff | True | 1 | - |
| registry_facility | True | 1 | - |
| registry_employer | True | 1 | - |
| registry_contact | True | 1 | - |
| registry_identifier | True | 1 | - |
| presidio_email | False | presidio | - |
| presidio_phone | False | presidio | - |
| presidio_url | False | presidio | - |
| presidio_date | False | presidio | - |
| presidio_us_mbi | False | presidio | - |
| presidio_us_npi | False | presidio | - |
| presidio_us_ssn | False | presidio | - |
| pattern_ssn | True | 1 | format_spec:SSA |
| pattern_ip_address | True | 1 | format_spec:IETF |
| pattern_license_cert | True | 1 | format_spec:state_DL_formats |
| pattern_vehicle | True | 1 | format_spec:ISO_3779 |
| pattern_device | True | 1 | format_spec:udi_serials |
| pattern_age_over_89 | True | 1 | format_spec:hipaa_safe_harbor |
| pattern_mac_address | True | 1 | format_spec:IEEE_802 |
| pattern_imei | True | 1 | format_spec:3GPP_TS_23.003 |
| pattern_iban | True | 1 | format_spec:ISO_13616 |
| pattern_geo_coordinates | True | 1 | format_spec:decimal_degrees |
| labelled_identifiers | True | 1 | format_spec:hipaa_safe_harbor_field_labels |
| labelled_payer_names | True | 1 | format_spec:payer_field_labels |

## Redaction and vault

- default operator `TOKENIZE`, token format `[{ENTITY}_{N}]`, consistency scope `conversation`
- vault backend `encrypted_file` (TTL 30 days, audit True, key `env:PHI_VAULT_KEY`)
- outbound guard: enabled True, action `block`, blocking entities 13
- model-hit cache entries retained by a serving engine: 0

## Evidence

Release gates (rc_2026-10-06): **NO-GO** - G1 INSUFFICIENT_EVIDENCE, G2 INSUFFICIENT_EVIDENCE, G3 INSUFFICIENT_EVIDENCE, G4 PASS, G5 PASS, G6 INSUFFICIENT_EVIDENCE, G7 PASS, G8 FAIL, G9 PASS

- CheckList (fast): 2227/2227 cases; G5 PASS
- CheckList (model): 75/75 cases; G5 PASS
- Performance (baseline_cpu_fast, device cpu): typical call p95 14.886 ms; G8 INSUFFICIENT_EVIDENCE
- Latest scored evaluation (run_20261007_152830_2815e1, test access False): cadence_dev_text: residual 0.0583, high-risk recall 0.9221; cadence_dev_asr: residual 0.1997, high-risk recall 0.789

## Known limitations

- **No governed real-data (T4) evaluation exists.** All measurements are on fabricated data; gates G1-G3/G6 are INSUFFICIENT_EVIDENCE and the release decision is NO-GO until T4 evidence is produced under governance.
- ASR input loses entities before detection (`ASR_destroyed`); residual PHI on ASR text is materially higher than on typed text.
- The contextual model's CPU latency on long calls exceeds the proposed SLO; see the performance evidence.
- Gate numbers are PROPOSED (configs/eval/gates.yaml status) until signed by the risk owner.
