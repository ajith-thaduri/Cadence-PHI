# Datasheet - `user_cases_v1`

_Generated from configs/eval/datasets.yaml (Gebru et al., Datasheets for Datasets)._

| field | value |
|---|---|
| role | dev |
| phi_class | fabricated |
| modality | note |
| language | en |
| path | `data/diagnostic/user_cases_v1.jsonl` |
| sha256 | `bd9c6d52cc03ae54ee64e51511328fb31a189f08193031732b22ceeca4771364` |
| provenance | user-supplied fabricated cases (2026-10-07) + span annotations; used to DISCOVER detection issues, so development data by definition - never a held-out measure. Scored with python -m harness_eval.strict_eval. |
| access_class | internal_fabricated |
| license | internal |
| retention | indefinite (fabricated data) |

## Use restrictions

Development only: rule development, calibration and debugging are permitted.
