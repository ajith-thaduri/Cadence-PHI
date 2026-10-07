# Datasheet - `cadence_asr`

_Generated from configs/eval/datasets.yaml (Gebru et al., Datasheets for Datasets)._

| field | value |
|---|---|
| role | test |
| phi_class | fabricated |
| modality | asr |
| language | en (filtered) |
| path | `data/cadence_realistic/cadence_asr.jsonl` |
| sha256 | `513e90ffd46bc7e1322a0e8a11b1f23e75eec173fdb644d1b6b4541c51d8672b` |
| provenance | cadence_text spoken docs -> TTS -> telephone channel -> Whisper-small (pinned); frozen test split |
| access_class | internal_fabricated |
| license | internal |
| retention | indefinite (fabricated data) |

## Use restrictions

Frozen held-out: measured once per release candidate; every access is ledgered; no tuning, no rule development.
