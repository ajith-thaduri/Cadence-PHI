# Model inspection

Inspection done on 2026-10-01, before any adapter was written. Sources: Hugging Face
API metadata, each repo's `config.json` / `gliner_config.json` / tokenizer config / README,
the linked papers and the authors' inference code. Each checkpoint was then **loaded
locally and probed** (results at the end) to confirm what the files claim.

Everything below is what the artifacts say. Our measured benchmark results are only in
`results/` and `reports/`.

---

## A. Stanford Deidentifier Base, `StanfordAIMI/stanford-deidentifier-base`

| Item | Finding |
|---|---|
| Revision evaluated | `661b9c1c717d3165512d440abc3700c386aefab6` (lastModified 2024-10-09) |
| Files | `pytorch_model.bin` (438 MB), `config.json`, `vocab.txt`, `tokenizer_config.json` (only `model_max_length: 512`), `special_tokens_map.json` |
| Architecture | BERT, 12 layers, hidden 768, 12 heads, vocab 30,522. `config.json` declares `architectures: ["AutoModel"]` but `model_type: bert` + `id2label`; `AutoModelForTokenClassification` loads `BertForTokenClassification` **with the classifier head present** (only `bert.pooler.*` unused). Measured parameters: **108,897,800**. |
| Tokenizer | `BertTokenizerFast`, `do_lower_case=True` (uncased; accents stripped). PubMedBERT-style English vocab. |
| Native labels | `O, VENDOR, DATE, HCW, HOSPITAL, ID, PATIENT, PHONE` (7 PHI labels) |
| Tag scheme | **IO**, with no B-/I- prefixes, so no BIO conversion is needed. Adjacent same-label wordpieces are merged into spans. |
| Max sequence length | 512 wordpiece tokens (`max_position_embeddings`) |
| Intended inference | `pipeline("token-classification")` with no aggregation (per-wordpiece argmax). Reports longer than 512 tokens are split into ≤508-token chunks at `:` `;` `.` tokens. Source: `MIDRC/Stanford_Penn_MIDRC_Deidentifier/deidentifier_model.py` @ `897fc0c`. The official pipeline also runs "hide in plain sight" surrogate replacement, which is out of scope here. |
| Input / output format | Raw text → per-token softmax over 8 classes. The adapter keeps every token probability so thresholds can be re-decoded. |
| License | MIT (model card) |
| Paper | Chambon et al., JAMIA 2022, doi:10.1093/jamia/ocac219 |
| Released vs paper checkpoint | The card says these are "the recommended ones among all available deidentifier weights". The paper compares several models, and the card does not say which row this checkpoint is. |
| Not covered natively | ADDRESS / CITY / STATE / ZIP / GEO, EMAIL, URL, AGE, LANGUAGE, EMPLOYER |

## B. Stanford Deidentifier V2, `StanfordAIMI/stanford-deidentifier-v2`

| Item | Finding |
|---|---|
| Revision evaluated | `244fba67a64894533487fe4031232749c44f6216` (lastModified 2025-11-19) |
| Architecture | `BertForTokenClassification`, fine-tuned from `stanford-deidentifier-base` (`_name_or_path`), transformers 4.21.1. Measured parameters: **108,898,569**. |
| Tokenizer | `BertTokenizer` (fast available), `do_lower_case=True`, `model_max_length=512` |
| Native labels | `O, VENDOR, DATE, HCW, HOSPITAL, ID, PATIENT, PHONE, AGE` (8 PHI labels; adds **AGE**) |
| Tag scheme | IO |
| Max sequence length | 512 wordpiece tokens |
| Intended inference | Same as Base. The model card only links the paper. |
| License | MIT |
| Paper | Prakash et al., arXiv:2511.04079. Trained on two large Stanford radiology corpora. |
| Released vs paper checkpoint | Not stated explicitly. |
| Mapping note | `AGE` has no canonical Cadence category, so it is **UNMAPPED** (not forced). Synthetic ages are gold *ignore spans*, which means AGE predictions on them count as neither TP nor FP. |

## C. LOGICAL GLiNER (as specified), `BioMike/logical-modern-gliner-bi-large-v1.0`

| Item | Finding |
|---|---|
| Revision evaluated | `2861fbf4702df37fc78219141135dca161fa5035` (lastModified 2024-12-27) |
| Files | `gliner_config.json`, `pytorch_model.bin` (2.1 GB), tokenizer files. **No README / model card, no license tag, no pipeline tag.** |
| Architecture | GLiNER **bi-encoder span model** (`BiEncoderSpanGLiNER`, `span_mode=markerV0`, `has_rnn=true`, `max_width=12`). Text encoder `answerdotai/ModernBERT-large` (28 layers, hidden 1024, 8,192 positions); label encoder `BAAI/bge-base-en-v1.5`. Measured parameters: **529,845,248**. |
| Tokenizer | ModernBERT `PreTrainedTokenizerFast`; GLiNER whitespace word splitter |
| Max sequence length | `max_len=2048` **words** (GLiNER truncates beyond that with only a Python warning). Encoder limit 8,192 tokens. |
| Native labels | **None documented.** |
| Intended inference | Undocumented for this checkpoint. The author's only Space, `BioMike/Logical_GLiNER` ("First-Order and Propositional Logic Problems with GLiNER", @ `e45f056`), loads the sibling `logical-modern-gliner-bi-base-v1.0` with `labels=["answer"]` to extract answers from logic puzzles. |
| License | **Not declared** |
| Released vs paper checkpoint | **Provenance mismatch.** The clinical LOGICAL paper ("Local Obfuscation by GLINER for Impartial Context Aware Lineage", Shivaprakash, **Shukla** et al., NIMHANS, arXiv:2510.19346) ships its tool at `github.com/Lekhansh/LOGIC-…`. That tool's `app.py` @ `be468e7` loads **`Lekhansh/modern-gliner-large-mednotes-deidentifier`**, not this checkpoint. This checkpoint also predates the paper by about 10 months. |
| How we evaluate it | Exactly as requested, with no substitution. We send the LOGICAL paper's 9 labels **zero-shot** (`configs/gliner/logical.yaml`) at threshold 0.3 (the value used in the author's Space and the base model card). The coverage matrix marks these labels `Z` (requested zero-shot, never shown to be trained), not `Y`. |

## D. Medical Notes GLiNER, `Lekhansh/modern-gliner-large-mednotes-deidentifier`

| Item | Finding |
|---|---|
| Revision evaluated | `222fe2b47dfc368c416b682a4e0a5c1ad9456447` (lastModified 2026-06-10) |
| Architecture | Same GLiNER bi-encoder as C, fine-tuned from `knowledgator/modern-gliner-bi-large-v1.0` (card `base_model`). Measured parameters: **529,845,248**. |
| Native labels (9) | `person, address, address state, address country, identification number, dates, languages, groups, company`. The card, the paper and the tool's `app.py` agree. |
| Label semantics (card / paper) | person = HCW, patient or others; address = locations smaller than a state (cities, landmarks…); identification number = any numeric/alphanumeric ID (the paper also lists phone numbers); dates = **only fully specified dates** ("Jan 2020" intentionally not caught); company = named places of occupation or health-care facilities; groups = religion / caste / political groups. |
| Max sequence length | 2,048 words per pass |
| Intended inference | `GLiNER.predict_entities(chunk, labels=<9 labels>, threshold=0.2)` on 2,000-word chunks with a 25-word overlap. Also sets `data_processor.transformer_tokenizer.add_prefix_space=True`. The harness mirrors all of this. |
| License | MIT |
| Training data | 1,045 human-annotated psychiatric-hospital records, 386 validation (card). The paper reports 2,849 train / 376 test *instances*. |
| Released vs paper checkpoint | The paper's tool loads this exact ID. The card's and paper's split counts differ, and neither states whether this revision is the paper's evaluated checkpoint. |
| Not covered natively | EMAIL and URL. The paper defined "email URL" but had **0** examples, and it is absent from the released label set. |
| Card disclaimers | "must not be used as a standalone anonymization tool"; not evaluated for other domains or for multilingual use. |

---

## v2 additions (inspected 2026-10-01)

| Key | Checkpoint @ revision | Architecture / labels | Licence | Load probe |
|---|---|---|---|---|
| stanford_i2b2 | StanfordAIMI/stanford-deidentifier-with-radiology-reports-and-i2b2 @ d9b30bc | BERT-base, same 7 IO labels as Base; trained with i2b2 per name | MIT | OK; tagged "maria"/PATIENT, "dr. patel"/HCW, phone, date |
| obi_roberta | obi/deid_roberta_i2b2 @ 78f2152 | RoBERTa-large, BILOU over 11 types (AGE, DATE, EMAIL, HOSP, ID, LOC, OTHERPHI, PATIENT, PATORG, PHONE, STAFF); trained on i2b2 2014 train | MIT | OK; all probe PHI found |
| kindlab_roberta | KindLab/roberta-deid @ 551c727 | RoBERTa-base, IO over AGE/CONTACT/DATE/ID/LOCATION/NAME/PROFESSION; **no tokenizer shipped** | not declared | **DEGENERATE**: weights load with no missing keys, but max P(non-O) < 1e-5 on every token for raw, word-split, upper/lower-case and i2b2-style inputs -> EXCLUDED |
| kg_pii_large | knowledgator/gliner-pii-large-v1.0 @ f847f54 | GLiNER uni-encoder TOKEN-level on DeBERTa-v3-large, max_len 768 words; ~45 trained PII labels, no generic `date` | Apache-2.0 | OK |
| kg_pii_base | knowledgator/gliner-pii-base-v1.0 @ 61726e0 | GLiNER uni-encoder span on DeBERTa-v3-small, max_len 2048 | Apache-2.0 | OK |
| kg_stream_pii | knowledgator/gliner-stream-pii-v1.0 @ e871777 | GLiNER streaming-span on Qwen3-0.6B, max_len 8192, cached sessions (`session_id`) | Apache-2.0 | OK; stateless + session API both verified |
| labbiomedica_es | LabBiomedica/roberta-base-biomedical-clinical-es-finetuned-deid @ f590c17 | RoBERTa-base (PlanTL clinical-es), BIO NAME/PROFESSION/LOCATION; real Colombian maternal records | Apache-2.0 | OK on Spanish; fragments English |

The BioMike checkpoint is now EXCLUDED as provenance-invalid (see section C). GLiNER repos
that also ship ONNX exports are loaded from the pinned local snapshot directory so the
unneeded ONNX files are never required.

Published real-data evidence was re-checked: the Pyclipse "RoBERTa" results belong to
kind-lab Transformer-DeID (not OBI); SHIELD's "AIMI v1/v2" are described as DeBERTa-based,
so their mapping to the BERT Stanford checkpoints is UNCERTAIN; Kuo et al. 2025 names
obi/deid_roberta_i2b2 exactly. Details: configs/published_evidence.yaml.

## Language capability (Spanish)

None of the four repos documents Spanish data. The Stanford models use an English uncased
biomedical WordPiece vocabulary. The GLiNER models use ModernBERT-large and
`bge-base-**en**-v1.5`, both English-centric. Spanish results are therefore measured behaviour
only. A multilingual-capable architecture is not evidence that these checkpoints are multilingual.

## Local load probe (before benchmarking)

Offline load on Apple M3 (MPS), torch 2.14.1, transformers 5.16.1, gliner 0.2.29. Probe text:
*"Maria Thompson spoke with Dr. Michael Carter at Sunrise Valley Medical Center on September
18, 2026. Her MRN is MRN-847291 and phone (555) 410-2298. She lives at 1450 Example Avenue,
Example City, CA 90000."*

| Model | Loaded | Observed |
|---|---|---|
| Stanford Base | yes | Tagged names, facility, date, the ID digits (not the `MRN` prefix) and the phone. **Address tagged `HOSPITAL`** (no address label). `Maria Thompson` tagged `HCW`. |
| Stanford V2 | yes | Same pattern as Base. |
| BioMike logical | yes | With the 9 PHI labels at 0.2 it returned only `1450 Example Avenue` (0.29). With the logic-QA probe (`labels=["answer"]`) it extracted answer options with scores 0.82–0.87, which is consistent with a logic-QA fine-tune. |
| MedNotes GLiNER | yes | person ×2, company, dates, identification number (phone, 0.42), address ×2, address state. **Missed `MRN-847291` and the ZIP.** |

These probe observations are not benchmark results. See the generated report.
