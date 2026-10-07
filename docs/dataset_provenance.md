# Dataset provenance

| Dataset | Evidence label | Licence / access | Status in this run | How obtained |
|---|---|---|---|---|
| Synthetic Cadence Benchmark v2 - dev | SYNTHETIC DATA (calibration) | Generated, fabricated values | RUN | `python -m harness.generate_synthetic` (seed 7770002, template family A) |
| Synthetic Cadence Benchmark v2 - test | SYNTHETIC DATA / OUR INTERNAL TEST | Generated, fabricated values | RUN | seed 20261002, template family B (disjoint from dev) |
| MEDDOCAN dev / test (IberLEF 2019) | PUBLIC CLINICAL CORPUS — real Spanish clinical case narratives with SYNTHETIC PHI | CC BY 4.0, Zenodo 4279323, sha256 `d0e4708b…eac3` | RUN (official dev for calibration, official test for evaluation; train unused) | `python -m harness.download_public` |
| n2c2/i2b2 2014 de-identification | REAL CLINICAL DATA | DUA via DBMI portal | NOT RUN — DATASET UNAVAILABLE | adapter: `--dataset i2b2_2014 --data-path ... --allow-local-public-dataset` |
| i2b2 2006 de-identification | REAL CLINICAL DATA | DUA | NOT RUN — DATASET UNAVAILABLE | adapter `i2b2_2006` |
| PhysioNet Deidentified Medical Text (gold, MIMIC-II nursing notes) | REAL CLINICAL DATA (surrogate PHI) | Credentialed + CITI + DUA (the open `deid` 1.1 software package does NOT include the text) | NOT RUN — DATASET UNAVAILABLE | adapter `physionet_deid` |
| SHIELD (Stanford, 2026) | REAL CLINICAL DATA (surrogate PHI) | Stanford Redivis, not freely accessible | NOT RUN — DATASET UNAVAILABLE | - |
| Unofficial re-uploads of i2b2 on Hugging Face | - | Would violate the n2c2 DUA | DELIBERATELY NOT USED | - |

No genuine Spanish clinical text with REAL PHI, and no real clinical conversational/ASR
transcripts with PHI annotations, were legally obtainable. Real conversational corpora that
are open (e.g. ACI-Bench, PriMock57) carry no PHI annotations and fabricated names, and
were not annotated by hand (no manual judgement in the benchmark loop).

MEDDOCAN mapping to the canonical taxonomy is in `harness/datasets/meddocan.py`. Ages,
sex, profession, country, other-subject attributes and bare relation words ("madre") are
ignore regions under `configs/redaction_policy.yaml`. MEDDOCAN annotates lone years as
dates; our policy does not redact years, so those count as misses for every pipeline.
