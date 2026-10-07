"""Build the v2 report (21 required sections) as renderer-neutral sections.

Every result table title carries an evidence tag. Synthetic and MEDDOCAN results are always
in separate tables; nothing is averaged across them.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from harness.config import benchmark_config, model_entry, models_registry, not_added_models, published_evidence
from harness.label_mapping import COVERAGE_SYMBOL, LabelMapping
from harness.reporting.analysis import (
    CONTEXT_CATS, FULL, GATE_CATS, TAG, TIER_NAMES, architecture_evidence, candidate_table, context_recall,
    evidence_tier, g, gate_table, ok, pct, recommend, stage_metric,
)
from harness.reporting.loader import display_name
from harness.shield.pipeline import STAGES
from harness.taxonomy import CANONICAL_LABELS, HIGH_RISK

TESTS = [("synthetic_test", TAG["syn"]), ("meddocan_test", TAG["med"])]
REPORT_CATS = ["NAME", "DOB", "DATE", "PHONE", "EMAIL", "ADDRESS", "CITY", "STATE", "ZIP", "MEMBER_ID", "MRN",
               "ACCOUNT_ID", "NPI", "PROVIDER_NAME", "FACILITY_NAME", "EMPLOYER", "RELATIVE_NAME", "OTHER_UNIQUE_ID",
               "GEOGRAPHIC_CONTEXT", "CARE_TEAM_MEMBER", "ORGANIZATION", "LANGUAGE", "URL"]
STAGE_LABEL = {"MODEL_ONLY": "Model only", "DETERMINISTIC_ONLY": "Deterministic only",
               "DETERMINISTIC_REGISTRY": "Deterministic + registry (no ML)", "MODEL_DETERMINISTIC": "Model + deterministic",
               "MODEL_DETERMINISTIC_REGISTRY": "Model + deterministic + registry (full shield)"}
GATE_NOTE = ("Gates are INTERNAL ENGINEERING BENCHMARK GATES (recall < 95% = ✗). They are not HIPAA compliance "
             "determinations; HIPAA compliance is an architectural/legal determination, not a model metric.")


def T(title, columns, rows, note=None):
    return {"title": title, "columns": columns, "rows": rows, "note": note}


def S(sid, title, paras=None, tables=None, bullets=None, images=None):
    return {"id": sid, "title": title, "paras": paras or [], "tables": tables or [], "bullets": bullets or [],
            "images": images or []}


def _names(run):
    return [display_name(k) for k in ok(run)]


def _row(run, label, fn, fmt=pct):
    return [label] + [fmt(fn(r)) for r in ok(run).values()]


def build_sections(run, plots: dict[str, str]) -> tuple[dict, list[dict]]:
    secs: list[dict] = []
    M = ok(run)
    names = _names(run)
    rec = recommend(run)
    arch = architecture_evidence(run)
    ds = run["datasets"]

    # 1 ------------------------------------------------------------------------------------
    pick = rec.get("pick") or {}
    secs.append(S("summary", "1. Executive Summary", paras=[
        "Goal: identify a pretrained clinical PHI/PII detector that performs reliably on REAL clinical text and can "
        "serve as the contextual ML layer of the Cadence PHI Shield (before AWS Bedrock). No model is declared a "
        "winner on synthetic numbers.",
        f"{TAG['unk']} No real-data reproduction was possible: i2b2 2006/2014 and the PhysioNet gold corpus need a "
        "DUA or credentialed access; SHIELD is not openly accessible. All are recorded as NOT RUN — DATASET UNAVAILABLE. "
        "The only public clinical corpus run was MEDDOCAN (Spanish case reports, synthetic PHI).",
        f"{TAG['pub']} The strongest independent real-clinical evidence for any candidate is for obi/deid_roberta_i2b2 "
        "(Kuo et al. 2025: names 0.979, dates 0.972, MRN 0.924, phone 0.994 recall on 3,650 UK hospital records). "
        "Published cross-institution studies show large name/location recall drops for i2b2-trained transformers "
        "and recall gains when ML is combined with rules.",
        gate_sentence(run, arch),
        f"{TAG['inf']} Model selection: {rec_statement(rec)}",
        "Architecture: use a contextual clinical ML model COMBINED with deterministic identifier detection and registry "
        "matching (section 21). Model selection and architecture are separate decisions.",
        "BioMike/logical-modern-gliner-bi-large-v1.0 is EXCLUDED as provenance-invalid (non-clinical logic-QA model). "
        "KindLab/roberta-deid is EXCLUDED because the released checkpoint is degenerate (predicts O everywhere).",
        GATE_NOTE]))

    # 2 ------------------------------------------------------------------------------------
    rows = []
    for k, e in models_registry().items():
        r = run["models"].get(k, {})
        md = r.get("metadata", {})
        rows.append([e["display_name"], e["model_id"], e["revision"], e.get("evaluation_role"),
                     str(e.get("provenance_status"))[:70], r.get("status", "EXCLUDED" if e.get("evaluation_role") == "excluded" else "-"),
                     e.get("architecture"), f"{(md.get('parameter_count_measured') or e.get('parameter_count') or 0):,}",
                     f"{(md.get('model_file_bytes') or 0) / 1e6:,.0f} MB" if md.get("model_file_bytes") else "n/a",
                     e.get("maximum_context"), e.get("license")])
    secs.append(S("models", "2. Models Tested", paras=[
        "Exact artifacts (Hugging Face model ID + commit). Full provenance fields are in configs/models.yaml and "
        "docs/model_inspection.md. Candidates reviewed but not added are listed with reasons."],
        tables=[T("Model provenance", ["Model", "HF ID", "Revision", "Role", "Provenance", "Status", "Architecture",
                                       "Params", "Weights", "Max input", "License"], rows),
                T("Reviewed but not added", ["Model", "Reason"], [[x["model_id"], x["reason"]] for x in not_added_models()]),
                T("Training data and clinical evidence (as documented)", ["Model", "Training data", "Clinical-data evidence", "Native labels"],
                  [[e["display_name"], e.get("training_data_description"), e.get("clinical_data_evidence"),
                    ", ".join(e.get("native_labels") or []) or "none documented / open-label"] for e in models_registry().values()])]))

    # 3 ------------------------------------------------------------------------------------
    pe = published_evidence()
    prow = [[display_name(e["model"]), e["source"], e["dataset"], "REAL CLINICAL DATA — PUBLISHED" if e["real_clinical"] else "SYNTHETIC / NON-CLINICAL",
             str(e.get("held_out")), "INDEPENDENT" if e["evaluator"] == "independent" else ("AUTHOR-REPORTED" if e["evaluator"] == "authors" else "THIRD PARTY, SAME INSTITUTION"),
             e["checkpoint_identity"], e["metric"], str(e["value"]), e.get("note", "")] for e in pe["entries"]]
    trow = [[display_name(k), TIER_NAMES[evidence_tier(k)]] for k in models_registry() if k in run["models"]]
    secs.append(S("realworld", "3. Real-World Evidence", paras=[
        f"{TAG['pub']} Numbers below were NOT measured by this harness. 'Checkpoint identity' records whether the "
        "publication evaluated exactly this Hugging Face artifact.",
        f"{TAG['real']} NONE. No legally obtainable real clinical PHI corpus was available locally; no published "
        "result was reproduced or refuted."],
        tables=[T(f"Published evaluations {TAG['pub']}", ["Model", "Source", "Dataset", "Data type", "Held out", "Evaluator",
                                                          "Checkpoint identity", "Metric", "Value", "Note"], prow),
                T("Real-clinical evidence tier (derived from the table above)", ["Model", "Tier"], trow),
                T(f"Field-level findings relevant to architecture {TAG['pub']}", ["Source", "Finding"],
                  [[f["source"], f["finding"]] for f in pe["field_findings"]])]))

    # 4 ------------------------------------------------------------------------------------
    drows = []
    labels = {"synthetic_dev": "SYNTHETIC DATA (calibration; template family A)",
              "synthetic_test": "SYNTHETIC DATA / OUR INTERNAL TEST (template family B)",
              "meddocan_dev": "PUBLIC CLINICAL CORPUS — real case text, SYNTHETIC PHI (official dev split)",
              "meddocan_test": "PUBLIC CLINICAL CORPUS — real case text, SYNTHETIC PHI (official test split)",
              "i2b2_2014": "REAL CLINICAL DATA (DUA required)", "i2b2_2006": "REAL CLINICAL DATA (DUA required)",
              "physionet_deid": "REAL CLINICAL DATA (credentialed access)"}
    for n, d in ds.items():
        drows.append([n, labels.get(n, ""), d.get("status"), d.get("documents", "-"), d.get("entities", "-"),
                      str(d.get("sha256", ""))[:16], d.get("reason", "; ".join(d.get("notes", []) or []))[:200]])
    drows.append(["SHIELD (Stanford, 2026)", "REAL CLINICAL DATA (surrogate PHI)", "NOT RUN — DATASET UNAVAILABLE", "-", "-", "-",
                  "Hosted on Stanford Redivis; not freely accessible."])
    secs.append(S("datasets", "4. Dataset Provenance", paras=[
        "Synthetic dev and test use DISJOINT template families (no template appears in both). No train split exists: no "
        "model is trained. MEDDOCAN (Zenodo 4279323, CC BY 4.0) keeps its official dev/test split; its texts are real "
        "published Spanish clinical case reports and its PHI values were inserted by documentalists - it is not real PHI.",
        "Redaction policy (configs/redaction_policy.yaml) governs gold, rules and scoring: relative dates are PHI; "
        "countries, ages, lone years, month/year durations, habitual weekdays, eponyms and clinical values are not."],
        tables=[T("Datasets", ["Dataset", "Evidence label", "Status", "Docs", "Entities", "SHA-256", "Notes"], drows)]))

    # 5 ------------------------------------------------------------------------------------
    tables = []
    for test, tag in TESTS:
        def mo(*p, test=test):
            return lambda r: stage_metric(r, test, "MODEL_ONLY_DEFAULT", *p)
        tables.append(T(f"Model only, default operating point — {test} {tag}", ["Metric"] + names, [
            _row(run, "A. Entity full-span recall", mo("metrics", "entity_recall")),
            _row(run, "B. Character-level PHI recall", mo("metrics", "char_recall")),
            _row(run, "C. Residual PHI character rate", mo("metrics", "residual_phi_character_rate")),
            _row(run, "D. Complete-document sanitization", mo("safety", "complete_document_sanitization_rate")),
            _row(run, "E. Precision (overlap)", mo("metrics", "detection", "overlap", "precision")),
            _row(run, "Over-redaction (non-PHI chars removed)", mo("safety", "over_redaction_char_rate"), lambda v: pct(v, 2)),
            _row(run, "High-risk recall", mo("metrics", "high_risk_recall")),
            _row(run, "Entity recall (overlap)", mo("metrics", "entity_recall_overlap")),
            _row(run, "F1 (overlap, label-agnostic)", mo("metrics", "detection", "overlap", "f1")),
            _row(run, "Typed micro F1", mo("metrics", "typed_micro", "f1")),
            _row(run, "False negatives (count)", mo("metrics", "false_negatives"), str),
            _row(run, "False positives (count)", mo("metrics", "false_positives"), str)]))
    neg = [_row(run, "Negative-control docs with any redaction (of 12)",
                lambda r: stage_metric(r, "synthetic_test", "MODEL_ONLY_DEFAULT", "safety", "negative_control_docs_with_any_redaction"), str),
           _row(run, "Negative-control redacted characters",
                lambda r: stage_metric(r, "synthetic_test", "MODEL_ONLY_DEFAULT", "safety", "negative_control_redacted_chars"), str)]
    tables.append(T(f"Over-redaction probes: NEGATIVE_CONTROL documents (no PHI: eponyms, 'United States', BP 120/80, "
                    f"'Monday through Friday', 1/2 tablet) {TAG['syn']}", ["Metric"] + names, neg))
    secs.append(S("overall", "5. Overall Metrics", paras=[
        "Entity full-span recall: an entity counts only if every core character (alphanumerics, excluding a leading title "
        "and literal ID affix words) is covered. Primary metrics are label-agnostic (a mislabelled name is still "
        "redacted). F1 is reported but never used alone."], tables=tables))

    # 6 ------------------------------------------------------------------------------------
    cov = {k: LabelMapping.load(k).coverage() for k in M}
    tables = []
    for test, tag in TESTS:
        rows = []
        for c in REPORT_CATS:
            sup = next(((stage_metric(r, test, "MODEL_ONLY_DEFAULT", "metrics", "per_category", c) or {}).get("support")
                        for r in M.values()), 0)
            if not sup:
                continue
            rows.append([("ACCOUNT_NUMBER" if c == "ACCOUNT_ID" else c) + (" *" if c in HIGH_RISK else ""), sup] + [
                f"{pct(stage_metric(r, test, 'MODEL_ONLY_DEFAULT', 'metrics', 'per_category', c, 'recall_full'))} [{COVERAGE_SYMBOL[cov[k][c]]}]"
                for k, r in M.items()])
        tables.append(T(f"Per-category full-span recall, model only — {test} {tag}", ["Category", "Support"] + names, rows))
    ctx = []
    for label, path in [("CONTEXTUAL_PHI subset", ("by_subset", "CONTEXTUAL_PHI")),
                        ("HARD_IDENTIFIERS subset", ("by_subset", "HARD_IDENTIFIERS")),
                        ("DATE_FORMS subset", ("by_subset", "DATE_FORMS")),
                        ("first-name-only mentions", ("by_entity_tag", "first_name_only")),
                        ("last-name-only mentions", ("by_entity_tag", "last_name_only")),
                        ("nicknames", ("by_entity_tag", "nickname")), ("relatives", ("by_entity_tag", "relative")),
                        ("provider/facility ambiguity", ("by_entity_tag", "provider_facility_ambiguity")),
                        ("partial addresses / intersections", ("by_entity_tag", "partial_address")),
                        ("natural ID formats", ("by_entity_tag", "natural_format")),
                        ("stylised ID formats (TEST-MBR-...)", ("by_entity_tag", "stylized_format")),
                        ("insurance-style IDs", ("by_entity_tag", "insurance_id")),
                        ("relative dates", ("by_entity_tag", "relative_date")),
                        ("bare weekday appointments", ("by_entity_tag", "bare_weekday")),
                        ("partial dates (Jan 2nd, 9/18)", ("by_entity_tag", "partial_date")),
                        ("malformed dates", ("by_entity_tag", "malformed_date"))]:
        for st in ("MODEL_ONLY_DEFAULT", FULL):
            ctx.append(_row(run, f"{label} — {'model only' if st != FULL else 'full shield'}",
                            lambda r, p=path, st=st: stage_metric(r, "synthetic_test", st, "metrics", *p, "recall_full")))
    tables.append(T(f"Contextual PHI, hard identifiers and date forms (full-span recall) {TAG['syn']}", ["Slice"] + names, ctx))
    tables.append(T("Native label coverage (Y full, P partial, Z zero-shot/open-label only, N none)", ["Canonical"] + names,
                    [[c] + [COVERAGE_SYMBOL[cov[k][c]] for k in M] for c in CANONICAL_LABELS]))
    secs.append(S("per_category", "6. Per-Category Recall", tables=tables, images=[plots.get("category_recall")]))

    # 7 ------------------------------------------------------------------------------------
    gcols = ["Model"] + [("PROVIDER" if c == "PROVIDER_NAME" else "FACILITY" if c == "FACILITY_NAME" else c) for c in GATE_CATS] + ["HIGH-RISK"]
    tables = []
    for test, tag in TESTS:
        tables.append(T(f"Critical-category gates, model only — {test} {tag}", gcols, gate_table(run, test, "MODEL_ONLY_DEFAULT")))
        tables.append(T(f"Critical-category gates, full shield — {test} {tag}", gcols, gate_table(run, test, FULL)))
    secs.append(S("high_risk", "7. High-Risk PHI Recall", paras=[GATE_NOTE, "A model with high overall F1 but one failing "
               "high-risk category is visible here: every ✗ is a category where >5% of entities would leak."], tables=tables))

    # 8 / 9 ---------------------------------------------------------------------------------
    t8, t9 = [], []
    for test, tag in TESTS:
        t8.append(T(f"Complete-document sanitization — {test} {tag}", ["Stage"] + names,
                    [_row(run, STAGE_LABEL[st], lambda r, st=st, test=test: stage_metric(r, test, st, "safety", "complete_document_sanitization_rate"))
                     for st in STAGES if st in ("MODEL_ONLY", "MODEL_DETERMINISTIC", FULL)] +
                    [_row(run, "False all-clear (PHI present, nothing redacted) - full shield",
                          lambda r, test=test: stage_metric(r, test, FULL, "safety", "false_all_clear_rate"))]))
        rows = []
        for st in ("MODEL_ONLY", "MODEL_DETERMINISTIC", FULL):
            for lab, key in [("mean", "mean"), ("median", "median"), ("P95", "p95"), ("max", "max")]:
                rows.append(_row(run, f"{STAGE_LABEL[st]} — {lab}",
                                 lambda r, st=st, key=key, test=test: stage_metric(r, test, st, "safety", "RESIDUAL_PHI_AFTER_SHIELD", key)))
            rows.append([f"{STAGE_LABEL[st]} — worst document"] +
                        [f"{stage_metric(r, test, st, 'safety', 'RESIDUAL_PHI_AFTER_SHIELD', 'worst_document')} "
                         f"({stage_metric(r, test, st, 'safety', 'RESIDUAL_PHI_AFTER_SHIELD', 'worst_document_residual_chars')} chars)"
                         for r in M.values()])
            rows.append(_row(run, f"{STAGE_LABEL[st]} — docs with residual PHI",
                             lambda r, st=st, test=test: stage_metric(r, test, st, "safety", "documents_with_residual_phi"), str))
        t9.append(T(f"RESIDUAL_PHI_AFTER_SHIELD (per-document fraction of gold PHI characters left after redaction) — {test} {tag}",
                    ["Stage / statistic"] + names, rows))
    secs.append(S("sanitization", "8. Complete-Document Sanitization", paras=[
        "Primary safety question: how often does PHI remain after the detector says the document is sanitized? Every "
        "document is redacted with consistent tokens ([NAME_1] ...) and the redacted output is compared with gold."], tables=t8))
    secs.append(S("residual", "9. Residual PHI", paras=["Computed on the actual redacted text: gold core characters "
                  "not inside any redacted region. Mean/median/P95 are over documents containing PHI."], tables=t9,
                  images=[plots.get("residual")]))

    # 10 ASR ---------------------------------------------------------------------------------
    rows = []
    for lab, path in [("NOISY_ASR subset", ("by_subset", "NOISY_ASR")), ("TELEPHONE_CONVERSATION", ("by_subset", "TELEPHONE_CONVERSATION")),
                      ("FACE_TO_FACE_DICTATION", ("by_subset", "FACE_TO_FACE_DICTATION")),
                      ("noise: clean", ("by_noise_level", "clean")), ("noise: medium", ("by_noise_level", "medium")),
                      ("noise: high", ("by_noise_level", "high")), ("spoken numbers", ("by_entity_tag", "spoken_number")),
                      ("accent spelling variation", ("by_entity_tag", "accent_variation"))]:
        for st in ("MODEL_ONLY_DEFAULT", FULL):
            rows.append(_row(run, f"{lab} — {'model only' if st != FULL else 'full shield'}",
                             lambda r, p=path, st=st: stage_metric(r, "synthetic_test", st, "metrics", *p, "recall_full")))
    strm = [[display_name(k), r["streaming"].get("status", "RUN"), pct(r["streaming"].get("stateless_recall_full")),
             pct(r["streaming"].get("streaming_recall_full")), r["streaming"].get("documents", "-"),
             f"{r['streaming']['mean_seconds_per_stream_chunk'] * 1000:.0f} ms" if r["streaming"].get("mean_seconds_per_stream_chunk") else "-"]
            for k, r in M.items() if r.get("streaming")]
    secs.append(S("asr", "10. ASR Results", paras=[f"{TAG['syn']} Synthetic ASR-style text only; no real ASR "
                  "transcripts with PHI annotation were available."],
                  tables=[T(f"ASR / conversational full-span recall {TAG['syn']}", ["Slice"] + names, rows),
                          T(f"Streaming (cached incremental session) vs stateless {TAG['syn']}",
                            ["Model", "Status", "Stateless recall", "Streaming recall", "Docs", "Latency / chunk"], strm)],
                  images=[plots.get("noise")]))

    # 11 Spanish -------------------------------------------------------------------------------
    rows = []
    for lab, test, path in [("Synthetic SPANISH subset", "synthetic_test", ("by_language", "es")),
                            ("Synthetic code-switched (mixed)", "synthetic_test", ("by_language", "mixed")),
                            ("MEDDOCAN test (all)", "meddocan_test", ("overall",))]:
        for st in ("MODEL_ONLY_DEFAULT", FULL):
            key = "recall_full"
            rows.append(_row(run, f"{lab} — {'model only' if st != FULL else 'full shield'}",
                             lambda r, p=path, st=st, test=test: stage_metric(r, test, st, "metrics", *p, key)))
    rows.append(_row(run, "MEDDOCAN test — precision (model only)",
                     lambda r: stage_metric(r, "meddocan_test", "MODEL_ONLY_DEFAULT", "metrics", "detection", "overlap", "precision")))
    secs.append(S("spanish", "11. Spanish Results", paras=[
        f"{TAG['unk']} Genuine Spanish clinical text WITH REAL PHI was not available. MEDDOCAN provides real Spanish "
        "clinical narrative with synthetic PHI; the synthetic SPANISH/MIXED subsets are fully synthetic.",
        "Only LabBiomedica documents Spanish training (real Colombian maternal records). No English model documents "
        "Spanish support; Spanish numbers below are measured behaviour, not a capability claim."],
        tables=[T(f"Spanish and code-switching full-span recall {TAG['syn']} / {TAG['med']}", ["Slice"] + names, rows)]))

    # 12 Long docs -------------------------------------------------------------------------------
    lrows, brows = [], []
    for k, r in M.items():
        by = {}
        for x in r.get("long_documents", []):
            by.setdefault((x["target_tokens"], x["strategy"]), []).append(x)
        for (tt, st), xs in sorted(by.items(), key=lambda kv: (kv[0][0], ["author", "production", "naive", "truncate"].index(kv[0][1]))):
            lrows.append([display_name(k), tt, st, f"{xs[0]['max_supported_input']} {xs[0]['unit']}",
                          int(sum(x["actual_length"] for x in xs) / len(xs)), "YES" if any(x["truncated"] for x in xs) else "no",
                          sum(x["entities_beyond_truncation"] for x in xs), sum(x["entities_split_by_chunker"] for x in xs),
                          pct(sum(x["recall_full"] for x in xs) / len(xs))])
        cb = r.get("chunk_boundary", {}).get("summary", {})
        for st in ("naive", "author", "production"):
            s = cb.get(st, {})
            brows.append([display_name(k), st, s.get("entities_split", "-"), pct(s.get("full_coverage_rate"))] +
                         [pct((s.get("by_case") or {}).get(c)) for c in ("name_split", "address_split", "phone_split", "mrn_split", "date_split")])
    secs.append(S("long", "12. Long-Document Results", paras=[
        f"{TAG['syn']} Sizes 500-16,000 target tokens. WITHOUT CHUNKING = 'truncate' (single pass; the model/library "
        "truncates at its window). WITH PRODUCTION CHUNKING = 'production' (overlapping context windows). 'author' = "
        "each model's own pipeline; 'naive' = hard cuts with no overlap.",
        "Chunk-boundary test: entities deliberately straddle the naive boundary (name, address, phone, MRN, date; 3 "
        "offsets each). 'Entities split' counts entities crossing a scored-region boundary; production chunking scores "
        "each token inside a window that includes context on both sides."],
        tables=[T(f"Long documents by size and strategy {TAG['syn']}", ["Model", "Target tokens", "Strategy", "Max input",
                  "Actual length", "Truncated", "Entities beyond boundary", "Entities split", "Recall"], lrows),
                T(f"Chunk-boundary split test {TAG['syn']}", ["Model", "Strategy", "Entities split", "Full coverage", "Name",
                  "Address", "Phone", "MRN", "Date"], brows)],
        images=[plots.get("long")]))

    # 13 Deterministic + ML ------------------------------------------------------------------------
    tables = []
    for test, tag in TESTS:
        b = run["baselines"].get(test) or {}
        rows = []
        for st in ("DETERMINISTIC_ONLY", "DETERMINISTIC_REGISTRY"):
            v = b.get(st) or {}
            rows.append([STAGE_LABEL[st] + " (no model)", pct(g(v, "metrics", "entity_recall")), pct(g(v, "metrics", "high_risk_recall")),
                         pct(g(v, "safety", "RESIDUAL_PHI_AFTER_SHIELD", "mean")), pct(g(v, "safety", "complete_document_sanitization_rate")),
                         pct(g(v, "metrics", "detection", "overlap", "precision")), pct(g(v, "safety", "over_redaction_char_rate"), 2)])
        for k, r in M.items():
            for st in ("MODEL_ONLY", "MODEL_DETERMINISTIC", FULL):
                rows.append([f"{display_name(k)} — {STAGE_LABEL[st]}",
                             pct(stage_metric(r, test, st, "metrics", "entity_recall")), pct(stage_metric(r, test, st, "metrics", "high_risk_recall")),
                             pct(stage_metric(r, test, st, "safety", "RESIDUAL_PHI_AFTER_SHIELD", "mean")),
                             pct(stage_metric(r, test, st, "safety", "complete_document_sanitization_rate")),
                             pct(stage_metric(r, test, st, "metrics", "detection", "overlap", "precision")),
                             pct(stage_metric(r, test, st, "safety", "over_redaction_char_rate"), 2)])
        tables.append(T(f"Stage comparison — {test} {tag}", ["Pipeline", "Entity recall", "High-risk recall",
                        "Residual PHI (mean/doc)", "Doc sanitization", "Precision", "Over-redaction"], rows))
    secs.append(S("det_ml", "13. Deterministic + ML Results", paras=[
        "Deterministic layer: phone, email, URL, member/insurance ID, MRN, NPI/professional licence, account, ZIP, dates, "
        "DOB, relative dates, addresses, titled names, spoken-digit normalisation (EN/ES). Rules were developed on the "
        "dev splits only. Stages use each model's shield threshold selected on dev.",
        f"{TAG['inf']} Caveat: the rules and the synthetic templates were written by the same author, so rule recall on "
        "synthetic test is optimistic even with disjoint template families; MEDDOCAN rule performance is the less biased estimate."],
        tables=tables, images=[plots.get("stages")]))

    # 14 Registry ---------------------------------------------------------------------------------
    rows = []
    for test, tag in TESTS:
        b = run["baselines"].get(test) or {}
        d, dr = b.get("DETERMINISTIC_ONLY") or {}, b.get("DETERMINISTIC_REGISTRY") or {}
        for c in ["NAME", "PROVIDER_NAME", "CARE_TEAM_MEMBER", "FACILITY_NAME", "RELATIVE_NAME", "EMPLOYER", "MEMBER_ID", "MRN", "PHONE"]:
            a1 = g(d, "metrics", "per_category", c)
            if not a1 or not a1.get("support"):
                continue
            rows.append([test, c, a1["support"], pct(a1["recall_full"]), pct(g(dr, "metrics", "per_category", c, "recall_full"))] +
                        [pct(stage_metric(r, test, "MODEL_DETERMINISTIC", "metrics", "per_category", c, "recall_full")) + " → " +
                         pct(stage_metric(r, test, FULL, "metrics", "per_category", c, "recall_full")) for r in M.values()])
    oor = [_row(run, "out-of-registry entities (provider2 / facility2) — full shield",
                lambda r: stage_metric(r, "synthetic_test", FULL, "metrics", "by_entity_tag", "out_of_registry", "recall_full"))]
    secs.append(S("registry", "14. Registry Matching Results", paras=[
        f"{TAG['syn']} Synthetic registries are realistic but incomplete: each document's member record, the member's "
        "primary provider, care-team staff, three facilities; relatives appear only 40% of the time (emergency "
        "contact), employers 50%, and a second provider/facility is never registered.",
        f"{TAG['med']} MEDDOCAN has no registry; its registry stage uses an ORACLE registry simulated from gold "
        "(patient/provider names and IDs) - an UPPER BOUND, not an estimate."],
        tables=[T("Registry effect by category (rules → rules+registry; per model: model+rules → full shield)",
                  ["Test set", "Category", "Support", "Rules only", "Rules + registry"] + [f"{n} (+rules → +registry)" for n in names], rows),
                T(f"Entities NOT in any registry {TAG['syn']}", ["Slice"] + names, oor)]))

    # 15 Thresholds --------------------------------------------------------------------------------
    rows = []
    for test, tag in TESTS:
        for k, r in M.items():
            cal = g(r, "pairs", test, "calibration") or {}
            rows.append([test, display_name(k), "argmax" if cal.get("default_threshold") is None else cal.get("default_threshold"),
                         _t(cal.get("best_dev_threshold_model_only")), _t(cal.get("best_dev_threshold_shield")),
                         pct(stage_metric(r, test, "MODEL_ONLY_DEFAULT", "metrics", "high_risk_recall")),
                         pct(g(r, "pairs", test, "model_only_dev_threshold", "metrics", "high_risk_recall")),
                         pct(g(r, "pairs", test, "model_only_dev_threshold", "metrics", "detection", "overlap", "precision")),
                         pct(stage_metric(r, test, FULL, "metrics", "high_risk_recall"))])
    secs.append(S("thresholds", "15. Threshold Analysis", paras=[
        "Thresholds are selected ONLY on dev (synthetic dev = disjoint template family; MEDDOCAN official dev) by max "
        "F2 of full-coverage detection, separately for the model alone and for the full shield, then applied unchanged "
        "to test. The test-set sweep is in the metrics JSON for transparency only. Token-level GLiNER (Knowledgator "
        "PII Large) is swept at 0.1/0.2/0.3/0.5/0.7 because each threshold needs a new inference pass."],
        tables=[T("Default vs best dev threshold (results on TEST)", ["Test set", "Model", "Default", "Best dev thr (model)",
                  "Best dev thr (shield)", "HR recall @default", "HR recall @dev thr", "Precision @dev thr", "HR recall full shield @shield thr"], rows)],
        images=[plots.get("pr")]))

    # 16 FN -----------------------------------------------------------------------------------------
    fn_tables = []
    for k, ex in run.get("_fn_examples", {}).items():
        if ex:
            fn_tables.append(T(f"{display_name(k)} — full-shield false negatives (high-risk first) {TAG['syn']}",
                               ["Document", "Type", "Gold", "Predicted", "Coverage", "Primary failure", "Context"],
                               [[f["document_id"], f["entity_type"], f["gold"], f.get("prediction") or "-", pct(f["coverage"], 0),
                                 f["primary_failure"], f["context"]] for f in ex]))
    secs.append(S("fn", "16. False Negatives", paras=[
        "Every false negative is exported per model, dataset and stage: false_negatives/<model>__<dataset>.jsonl "
        "(model only) and ...__full_shield.jsonl, plus false_negatives.csv. Fields: document ID, entity type, gold "
        "span, predicted span, coverage, context, dataset, subset, failure categories. Programmatic; no manual edits."],
        tables=fn_tables))

    # 17 Failure analysis ----------------------------------------------------------------------------
    cats = ["model_missed_entity", "wrong_entity_boundary", "wrong_entity_type", "context_failure", "asr_failure",
            "long_document_failure", "identifier_format_failure", "spanish_failure", "registry_matching_failure",
            "chunk_boundary_failure"]
    tables = []
    for test, tag in TESTS:
        for field, lab in [("model_only_failure_counts", "model only, primary category"),
                           ("full_shield_failure_counts", "full shield, primary category")]:
            tables.append(T(f"Failure analysis — {test}, {lab} {tag}", ["Failure category"] + names,
                            [[c] + [str(g(r, "pairs", test, field, c, default=0)) for r in M.values()] for c in cats]))
    secs.append(S("failures", "17. Failure Analysis", paras=[
        "Each FN gets all applicable categories and one primary category (precedence: chunk boundary > long document > "
        "registry > ASR > identifier format > context > Spanish > wrong boundary > wrong type > missed)."], tables=tables,
        images=[plots.get("failures")]))

    # 18 Deployment -------------------------------------------------------------------------------------
    rows = []
    for k in M:
        for dev, L in (("MPS", run["latency"].get(k)), ("CPU", run["latency_cpu"].get(k))):
            if not L or "single_doc" not in L:
                rows.append([display_name(k), dev, "not benchmarked"] + ["-"] * 6)
                continue
            rows.append([display_name(k), dev, f"{L['load_time_s']:.1f} s",
                         f"{(L.get('cold_start') or {}).get('cold_start_total_s', 0):.1f} s" if L.get("cold_start") else "-",
                         f"{L['single_doc']['median_ms']:.0f} ms", f"{L['single_doc']['p95_ms']:.0f} ms",
                         f"{L['docs_per_second_batch']:.1f}", f"{L.get('peak_rss_mb', 0):,.0f} MB",
                         f"{(L.get('model_file_bytes') or 0) / 1e6:,.0f} MB"])
    secs.append(S("deployment", "18. Deployment Characteristics", paras=[
        f"Measured on {g(run, 'config', 'hardware', 'cpu_brand', default='this machine')} (RAM "
        f"{g(run, 'config', 'hardware', 'ram_total_gb', default='?')} GB). ≥3 warm-up and ≥20 measured iterations on "
        "Cadence-sized documents; download time excluded.",
        f"{TAG['inf']} AWS: BERT-base/RoBERTa-base models are CPU-feasible (e.g. ECS/Fargate or EC2 c-class) inside the "
        "VPC; GLiNER-large, DeBERTa-large and the Qwen3-based streaming model need a GPU instance (g5/g6) or tolerate "
        "seconds-level CPU latency. All models run fully offline; none requires an external API."],
        tables=[T("Latency and memory", ["Model", "Device", "Load", "Cold start", "Median", "P95", "Docs/s (batch 8)",
                                          "Peak RSS", "Weights"], rows)], images=[plots.get("latency")]))

    # 19 Licensing ---------------------------------------------------------------------------------------
    secs.append(S("licensing", "19. Licensing", paras=[f"{TAG['inf']} Licence strings are from the model repositories; "
                  "this is not legal advice. Training-data terms (e.g. i2b2 DUA) govern the data, not necessarily the "
                  "released weights - confirm with counsel."],
                  tables=[T("Licences", ["Model", "Licence", "Commercial use", "Fine-tunable on Cadence data"],
                            [[e["display_name"], e.get("license"), e.get("commercial_use"),
                              "yes (token classification)" if e["adapter"] == "hf_token_classification" else "yes (GLiNER training API)"]
                             for e in models_registry().values()])]))

    # 20 Limitations ---------------------------------------------------------------------------------------
    secs.append(S("limitations", "20. Limitations", bullets=LIMITATIONS))

    # 21 Recommendation -------------------------------------------------------------------------------------
    secs.append(recommendation_section(run, rec, arch))
    meta = {"title": "Clinical PHI Model Benchmark v2", "generated": datetime.now().isoformat(timespec="seconds"),
            "run_dir": run["run_dir"], "dataset": "synthetic_test + meddocan_test (reported separately)",
            "documents": f"{g(ds, 'synthetic_test', 'documents')} + {g(ds, 'meddocan_test', 'documents')}"}
    return meta, secs


def gate_sentence(run, arch) -> str:
    failing = []
    for k, r in ok(run).items():
        rows = gate_table(run, "synthetic_test", "MODEL_ONLY_DEFAULT")
    fails = {row[0]: sum("✗" in c for c in row[1:]) for row in gate_table(run, "synthetic_test", "MODEL_ONLY_DEFAULT")}
    full = {row[0]: sum("✗" in c for c in row[1:]) for row in gate_table(run, "synthetic_test", FULL)}
    n_fail = sum(1 for v in fails.values() if v)
    n_full_fail = sum(1 for v in full.values() if v)
    s = (f"{TAG['syn']} Model alone: {n_fail} of {len(fails)} models fail at least one critical-category gate on the "
         f"synthetic test; with the full shield, {n_full_fail} of {len(full)} still fail at least one. ")
    s += ("Adding deterministic rules and registry matching reduced RESIDUAL_PHI_AFTER_SHIELD for every model on both test sets."
          if arch["combined_better_everywhere"] else "The combined pipeline did NOT reduce residual PHI for every model (section 13).")
    return s


def gate_counts(run) -> str:
    mo = sum(any("✗" in c for c in row[1:]) for row in gate_table(run, "synthetic_test", "MODEL_ONLY_DEFAULT"))
    fu = sum(any("✗" in c for c in row[1:]) for row in gate_table(run, "synthetic_test", FULL))
    n = len(ok(run))
    return (f"On the synthetic test, {mo} of {n} models alone fail at least one critical-category gate; "
            f"with the full shield {fu} of {n} still fail at least one.")


def _t(v):
    return "argmax" if v is None else v


def rec_statement(rec) -> str:
    if not rec.get("pick"):
        return "no eligible model."
    p = rec["pick"]
    return ("No single model is sufficiently validated for production. The evidence supports using "
            f"{p['name']} ({model_entry(p['key'])['model_id']}) as the contextual ML candidate while deterministic "
            "rules and registry matching handle structured identifiers, followed by validation on Cadence-specific "
            "annotated data.")


def recommendation_section(run, rec, arch) -> dict:
    rows = []
    for r in sorted(rec.get("rows", []), key=lambda r: (r["tier"], r["syn_full_resid"] if r["syn_full_resid"] is not None else 9)):
        rows.append([r["name"], TIER_NAMES[r["tier"]].split(" - ")[0], "yes" if r["license_ok"] else "NO/unknown",
                     "yes" if r["english"] else "no", pct(r["syn_model_hr"]), pct(r["syn_model_ctx"]), pct(r["syn_asr"]),
                     pct(r["syn_full_hr"]), pct(r["syn_full_resid"], 2), pct(r["syn_full_san"]), pct(r["syn_full_prec"]),
                     str(r["neg_docs_redacted"]), pct(r["med_model_hr"]), pct(r["med_modeldet_resid"], 2), pct(r["med_full_resid"], 2),
                     "-" if r["cpu_p95"] is None else f"{r['cpu_p95']:.0f} ms"])
    p, mb, es = rec.get("pick") or {}, rec.get("measured_best_any_tier") or {}, rec.get("spanish_best_full_shield") or {}
    arch_rows = [[display_name(x["key"]), x["test"], pct(x["model_resid"], 2), pct(x["full_resid"], 2), pct(x["model_san"]),
                  pct(x["full_san"]), pct(x["model_prec"]), pct(x["full_prec"])] for x in arch["per_model"]]
    for t, v in arch["baselines"].items():
        for st, x in v.items():
            arch_rows.append([STAGE_LABEL[st].replace(" (no ML)", "") + " (no ML)", t, "-", pct(x["resid"], 2), "-", pct(x["san"]), "-", "-"])
    added = arch["ml_adds_over_rules_registry"]
    bullets = [
        f"MODEL SELECTION {TAG['inf']}: {rec_statement(rec)}",
        f"Why {p.get('name')}: it is in the best available real-clinical evidence tier among eligible English models "
        f"({TIER_NAMES.get(rec.get('best_tier'), '?')}) and, within that tier, has the lowest residual PHI after the full "
        f"shield on our synthetic test ({pct(p.get('syn_full_resid'), 2)} mean per document). [OUR SYNTHETIC BENCHMARK] numbers are "
        "supporting evidence only, never proof of real-world performance.",
        f"Lowest full-shield residual on our synthetic test regardless of evidence tier: {mb.get('name')} "
        f"({pct(mb.get('syn_full_resid'), 2)}). " + ("Same model." if mb.get("key") == p.get("key") else
        "It ranks lower because its real-clinical evidence is weaker; treat it as an alternative to validate on Cadence data."),
        f"Spanish: lowest MEDDOCAN residual with model + rules (no registry - the MEDDOCAN registry is an oracle) is "
        f"{es.get('name')} ({pct(es.get('med_modeldet_resid'), 2)}) {TAG['med']}. No model has real-PHI Spanish validation; "
        "Spanish calls need Cadence-specific validation before relying on any model.",
        "Known weaknesses of the selected model on our tests (bottom half of eligible models): " +
        ("; ".join(f"{lab} {pct(v) if isinstance(v, float) else v} (rank {rk}/{n})" for lab, v, rk, n in rec.get("pick_weaknesses", []))
         or "none in the bottom half") + ". These are the first things to validate on Cadence data.",
        "Validation shortlist (decide on Cadence data, not on this benchmark): " +
        "; ".join(f"{display_name(k)} - {why}" for k, why in rec.get("shortlist", [])) +
        ". Real-world evidence favours the first; our conversational/ASR stress tests favour the others. Cadence call "
        "transcripts are conversational, so this disagreement must be resolved on annotated Cadence data.",
        "Before production: (1) obtain a DUA copy of n2c2 2014 and rerun `--dataset i2b2_2014` to produce [OUR REAL-DATA "
        "REPRODUCTION]; (2) double-annotate an IRB-approved sample of Cadence transcripts/notes (incl. Spanish and ASR) and "
        "measure RESIDUAL_PHI_AFTER_SHIELD; (3) fine-tune the selected model on that data if residual PHI exceeds the gate; "
        "(4) keep the deterministic + registry layers regardless of model.",
    ]
    final = [
        "QUESTION: \"For Cadence PHI Shield, should we use one clinical NER/de-identification model alone, or should we use "
        "a contextual clinical ML model combined with deterministic identifier detection and registry matching?\"",
        "ANSWER: Use a contextual clinical ML model COMBINED with deterministic identifier detection and registry matching.",
        f"{TAG['syn']} {TAG['med']} Evidence: " + ("for every evaluated model on both test sets, the full shield left less "
        "residual PHI than the model alone (see table). " if arch["combined_better_everywhere"] else
        "the full shield did NOT reduce residual PHI for every model (see table). ") + gate_counts(run) +
        " Rules + registry without ML also left more residual PHI than the full "
        f"shield for {len(added.get('synthetic_test', []))} of {len(ok(run))} models on synthetic test and "
        f"{len(added.get('meddocan_test', []))} on MEDDOCAN - the ML layer is what catches unregistered relatives, "
        "out-of-network providers, employers and contextual names.",
        f"{TAG['pub']} Consistent with published field evidence: ML + rules reached 0.988 recall on i2b2 2014 (Pyclipse), "
        "and institution-specific entities transfer poorly across sites (SHIELD), which registries address directly.",
        "MODEL SELECTION ≠ ARCHITECTURE: the architecture recommendation holds whichever model is chosen; the model "
        "choice above is provisional until a real-data reproduction exists.",
        "Bedrock runs under a BAA/HIPAA-eligible setup; this architecture is defense in depth to minimise PHI exposure. "
        "No model or pipeline here is 'HIPAA compliant' - that is an architectural/legal determination.",
    ]
    return S("recommendation", "21. Recommendation", paras=final, bullets=bullets, tables=[
        T("Recommendation inputs (no combined score; each column reported separately)",
          ["Model", "Evidence tier", "Licence OK", "English", "HR recall model [SYN]", "Contextual-name recall model [SYN]",
           "ASR recall model [SYN]", "HR recall full shield [SYN]", "Residual PHI full shield [SYN]", "Doc sanitization full [SYN]",
           "Precision full [SYN]", "Neg-control docs redacted [SYN]", "HR recall model [MEDDOCAN]", "Residual model+rules [MEDDOCAN]",
           "Residual full, ORACLE registry [MEDDOCAN]", "CPU P95"], rows),
        T("Architecture evidence: model alone vs full shield (residual = mean per-document RESIDUAL_PHI_AFTER_SHIELD)",
          ["Pipeline", "Test set", "Residual model only", "Residual full shield", "Doc san. model", "Doc san. full", "Precision model", "Precision full"], arch_rows)])


LIMITATIONS = [
    "[UNKNOWN] No real clinical PHI corpus could be evaluated (i2b2 2006/2014, PhysioNet gold, SHIELD all restricted). "
    "Real-world performance of every model on Cadence text is therefore UNKNOWN; only published evidence speaks to it.",
    "MEDDOCAN has real clinical narrative but synthetic, documentalist-inserted PHI in semi-structured headers; it "
    "over-represents formatted identifiers and under-represents conversational PHI. Its registry stage is an oracle upper bound.",
    "Synthetic data: template-generated by the same author who wrote the deterministic rules. Dev/test template families "
    "are disjoint, but authorship bias makes synthetic rule recall optimistic.",
    "Synthetic ASR is simulated text noise, not real speech-recognition output; real ASR errors (homophones, digit "
    "grouping, word splits) are more varied.",
    "Category supports are small for several labels (NPI, URL, STATE, ZIP, OTHER_UNIQUE_ID on synthetic test); single "
    "entities move recall by several points.",
    "Gold annotation follows configs/redaction_policy.yaml; a different policy (e.g. not redacting relative dates or "
    "states) changes DATE/STATE numbers. MEDDOCAN annotates lone years as dates; our policy does not redact them.",
    "Knowledgator models were prompted with the identifier labels from their cards; other label prompts could change "
    "results. Streaming model labels are open-label (training vocabulary undocumented).",
    "OBI RoBERTa was run with a 64-token core / 32-token context approximation of its spaCy-sentencized 128-token "
    "training sequences, not its original robust-deid preprocessing.",
    "KindLab/roberta-deid could not be evaluated (degenerate checkpoint); its published Pyclipse evidence may refer to a "
    "different working checkpoint.",
    "Latency measured on one Apple-silicon workstation (MPS + CPU); AWS instance performance will differ.",
]
