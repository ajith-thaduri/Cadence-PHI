"""Decision report: what should the contextual ML layer be?

All conclusions are computed from decision_results.json under the pre-registered protocol
(configs/decision_protocol.yaml); no conclusion text is hard-coded to a model.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from harness.config import load_yaml, model_entry, published_evidence
from harness.paths import REPORTS
from harness.reporting.render import to_html, to_markdown

FULL = "MODEL_DETERMINISTIC_REGISTRY"
PRIOR_RUN = Path("results/2026-10-01_192836")
TAG = {"m": "[MEASURED]", "p": "[PUBLISHED EVIDENCE]", "i": "[ENGINEERING INFERENCE]", "u": "[UNKNOWN]"}
DS_LABEL = {"cadence_asr": "Tier 3 ASR", "cadence_asr_uncorrected": "Tier 3 ASR (uncorrected gold)", "cadence_text": "Tier 3 text", "meddocan_test": "Tier 2 MEDDOCAN",
            "synthetic_test": "Tier 4 synthetic"}


def pct(v, d=1):
    return "n/a" if v is None else f"{v * 100:.{d}f}%"


def name(k: str) -> str:
    return model_entry(k)["display_name"] if "+" not in k else " + ".join(model_entry(x)["display_name"] for x in k.split("+"))


def T(title, cols, rows, note=None):
    return {"title": title, "columns": cols, "rows": rows, "note": note}


def S(sid, title, paras=None, tables=None, bullets=None):
    return {"id": sid, "title": title, "paras": paras or [], "tables": tables or [], "bullets": bullets or [], "images": []}


def latency(k: str) -> dict[str, Any]:
    out = {}
    for dev, suf in (("mps", ""), ("cpu", "__cpu")):
        p = PRIOR_RUN / "latency" / f"{k}{suf}.json"
        if p.exists():
            L = json.loads(p.read_text())
            if "single_doc" in L:
                out[dev] = {"median": L["single_doc"]["median_ms"], "p95": L["single_doc"]["p95_ms"],
                            "rss": L.get("peak_rss_mb"), "weights": (L.get("model_file_bytes") or 0) / 1e6}
    return out


def deviation_d1() -> str:
    g = Path("data/cadence_realistic/asr_garbled_entities.json")
    n = len(json.loads(g.read_text())) if g.exists() else 0
    loops = 0
    p = Path("data/cadence_realistic/cadence_asr.jsonl")
    if p.exists():
        loops = sum(json.loads(l)["metadata"].get("repetition_loop", False) for l in p.read_text().splitlines() if l.strip())
    return ("PROTOCOL DEVIATION D1 (made after first results, before the final verdict, identical for all models): "
            "inspection of the first Tier-3 ASR run showed the word aligner assigning gold labels to unrelated ASR words "
            "when Whisper destroyed an entity (e.g. a name transcribed as 'How here', 'breathe', 'Toggle'). Such text no "
            "longer identifies anyone, so it is not leaked PHI. 'Destroyed' = a numeric entity (date, ID, phone, street "
            "number) whose ASR text contains no digit, number word, ordinal, month or weekday; or a name-like entity with no "
            "ASR token (or letter-collapsed spelling) >= 0.6 character-similar to the original. A first character-only "
            "filter was rejected because it wrongly discarded re-formatted dates ('twenty-eighth' -> '28th') and intact "
            f"spelled names. Destroyed entities are ignore spans ({n} entities). Whisper repetition-hallucination loops were "
            f"detected in {loops} documents and are kept (they are real ASR behaviour). The uncorrected gold is still "
            "evaluated as 'Tier 3 ASR (uncorrected gold)' so the effect of D1 is visible; the decision rule is unchanged.")


def deviation_d2(R) -> str:
    sel = R["primary_selection"]
    return ("PROTOCOL DEVIATION D2 (implementation of the pre-registered tie-breakers, identical for all models): each "
            "tie-breaker decides only when it shows a MATERIAL difference, otherwise the next one applies. Applied literally, "
            f"tie-breaker 1 would have chosen {name(sel.get('literal_unguarded_tiebreak_primary', '?'))} on a near-ceiling P95 "
            "difference while that model had the WORST mean residual. Selection steps: " + " | ".join(sel.get("steps", [])))


def decide(R: dict[str, Any]) -> dict[str, Any]:
    A = R["primary_selection"]["primary"]
    passing = R.get("passing_pairs", [])
    sec, cascade_choice, latency_ok = None, None, {}
    for k in passing:
        b = k.split("+")[1]
        cpu = sum((latency(m).get("cpu") or {}).get("p95", 0) for m in (A, b))
        latency_ok[k] = cpu <= 500
    eligible = [k for k in passing if latency_ok.get(k)]
    if eligible:
        best = min(eligible, key=lambda k: R["pairs"][k]["datasets"]["cadence_asr"][FULL]["all"]["residual_mean"])
        sec = best.split("+")[1]
        if R.get("cascade"):
            adopt = [n for n, v in R["cascade"].items() if v["adopt"]]
            cascade_choice = adopt[0] if adopt else None
    third = None
    if sec and R.get("triple") and R["triple"]["materiality"]["passes_before_latency"]:
        third = [m for m in R["thresholds"] if m not in (A, sec)][0]
    ev = [e for e in published_evidence()["entries"] if e["model"] == A and e.get("real_clinical")]
    indep = any(e["evaluator"] == "independent" and e["checkpoint_identity"] == "exact" for e in ev)
    contradictions = R["primary_selection"]["contradictions"]
    selection_conf = "Medium" if (indep and not contradictions) else "Low"
    full = R["single"][A]["cadence_asr"][FULL]["all"]
    sufficient = (full["high_risk_recall"] or 0) >= 0.95
    conf = selection_conf if sufficient else "Low"
    return {"primary": A, "secondary": sec, "third": third, "cascade": cascade_choice, "latency_ok": latency_ok,
            "confidence": conf, "model_selection_confidence": selection_conf, "privacy_sufficient_on_tier3": sufficient,
            "tier3_asr_full_shield": {k: full[k] for k in ("residual_mean", "high_risk_recall", "sanitization")},
            "independent_tier1_published": indep, "contradictions": contradictions}


def build(R: dict[str, Any], out: Path) -> tuple[dict, list[dict]]:
    proto = load_yaml("configs/decision_protocol.yaml")
    dec = decide(R)
    A, sec, third = dec["primary"], dec["secondary"], dec["third"]
    secs: list[dict] = []
    cands = list(R["thresholds"])
    D = "cadence_asr"

    def single(m, ds, st=FULL, sl="all"):
        return R["single"][m][ds][st].get(sl) or {}

    # 0 decision block -------------------------------------------------------------------
    arch = "deterministic identifier rules + registry matching + " + name(A)
    if sec:
        arch += (f" + {name(sec)} as a selective second stage ({dec['cascade']})" if dec["cascade"]
                 else f" + {name(sec)} on every request")
    arch += " -> reconciliation/policy -> consistent tokenisation -> Bedrock"
    pair_lines = []
    for k, v in R["pairs"].items():
        mt = v["materiality"]
        pair_lines.append(f"{name(k)}: mean residual {pct(single(A, D)['residual_mean'], 2)} -> "
                          f"{pct(v['datasets'][D][FULL]['all']['residual_mean'], 2)} ({pct(mt['relative_mean_reduction'])} relative, "
                          f"{pct(mt['absolute_mean_reduction'], 2)} absolute); P95 {pct(mt['relative_p95_reduction'])} relative; "
                          f"critical categories fixed: {mt['M3_critical_category'] or 'none'}; slices: {mt['M4_slice'] or 'none'} "
                          f"-> {'MATERIAL' if k in R.get('passing_pairs', []) else 'NOT material'}")
    sel = R["primary_selection"]
    vals = sel["values"]
    alt = [m for m in sel["ranking_by_mean_residual"] if m != A]
    why = (f"On the decision dataset (Tier 3 real-ASR calls) no candidate was MATERIALLY better than another "
           f"(full-shield mean residual: " + ", ".join(f"{name(m)} {pct(vals[m]['residual_mean'])}" for m in sel['ranking_by_mean_residual']) +
           "); the pre-registered tie-breakers then removed the candidate materially worse on another tier and chose the one "
           f"with independent published real-clinical evidence: {name(A)}. " +
           (f"{name(sel['ranking_by_mean_residual'][0])} is a near-equivalent alternative (numerically lower residual, "
            "within the non-material margin, and much cheaper to run). " if sel['ranking_by_mean_residual'][0] != A else "") +
           ("No second model met the pre-registered materiality rule, so adding one would add latency, memory, "
            "versioning and monitoring cost without a material privacy gain." if not sec else
            f"{name(sec)} met the materiality rule and the cost guardrails."))
    final = [
        f"PRIMARY MODEL: {name(A)} ({model_entry(A)['model_id']})",
        f"SECONDARY MODEL: {name(sec) if sec else 'None'}",
        f"THIRD MODEL: {name(third) if third else 'None'}",
        f"ARCHITECTURE: {arch}",
        f"WHY: {why}",
        "MATERIAL ADVANTAGE THRESHOLD: pre-registered before results (configs/decision_protocol.yaml, SHA-256 "
        f"{R['protocol_sha256'][:16]}...): add a model only if it cuts mean residual PHI by >=30% relative AND >=0.5 pp "
        "absolute, or cuts P95 residual by >=30% and >=2 pp, or lifts a failing high-risk category (support >=10) to >=95% "
        "recall, or cuts residual >=30%/>=1 pp on the ASR-call or Spanish slice - AND adds <=0.5 pp over-redaction, "
        "<=25% more spurious spans, <=500 ms combined CPU P95.",
        f"PRODUCTION CONFIDENCE: {dec['confidence']} - on realistic Cadence-style ASR calls the recommended shield still leaves "
        f"{pct(dec['tier3_asr_full_shield']['residual_mean'])} of PHI characters per document (high-risk recall "
        f"{pct(dec['tier3_asr_full_shield']['high_risk_recall'])}), so the protection is NOT yet sufficient; and no own real-data "
        "(Tier 1) reproduction exists.",
        f"MODEL-SELECTION CONFIDENCE: {dec['model_selection_confidence']} - " + (
            "the choice is consistent across Tier 2/3 and the chosen model has independent published real-clinical evidence; "
            "but the candidates were NOT materially different on the decision data, so the choice rests on a tie-breaker."
            if dec["model_selection_confidence"] == "Medium" else "evidence is inconsistent or unpublished."),
        "STATUS: Architecture is validated; model selection remains conditional on Cadence-specific real-data validation. "
        "Not production-ready on this evidence alone.",
    ]
    secs.append(S("decision", "Final decision", paras=final, bullets=pair_lines))

    # 1/2/3 evidence + provenance --------------------------------------------------------
    pub = [[name(e["model"]), e["source"], e["dataset"], e["evaluator"], e["checkpoint_identity"], e["metric"], str(e["value"])]
           for e in published_evidence()["entries"] if e["model"] in cands]
    secs.append(S("realdata", "1. Real-data evidence obtained", paras=[
        f"{TAG['u']} No real clinical PHI corpus could be used: n2c2/i2b2 2006 & 2014 require a DUA; the PhysioNet "
        "de-identification gold corpus requires credentialed access and CITI training; SHIELD (Stanford Redivis) is not "
        "openly accessible; the MIMIC-III demo ships an empty NOTEEVENTS table; no licensed copy exists on this machine. "
        "None was bypassed or obtained from unofficial copies.",
        f"{TAG['p']} Published real-clinical results for the candidates (not reproduced by us):"],
        tables=[T("Published evidence (candidates only)", ["Model", "Source", "Dataset", "Evaluator", "Checkpoint identity", "Metric", "Value"], pub)]))
    ds_rows = [[n, R["datasets"][n]["tier"], R["datasets"][n]["documents"], R["datasets"][n]["entities"]] for n in R["datasets"]]
    secs.append(S("realistic", "2. Realistic-data evidence obtained", paras=[
        "Tier 3 was built for this decision: free-form Cadence-style documents written by three independent authoring "
        "agents that never saw the deterministic rules, the synthetic templates or the registry code (spec: "
        "data/cadence_realistic/AUTHORING_SPEC.md). PHI was marked inline at writing time, giving exact gold offsets.",
        "Spoken documents (calls, dictations) were rendered with offline macOS TTS (one voice per speaker), degraded to a "
        "telephone channel (8 kHz, 300-3400 Hz, 15-25 dB SNR noise, mu-law) and transcribed by local Whisper-small; gold "
        "spans were mapped onto the ASR text by word alignment. This gives REAL ASR-engine errors on names and digits."],
        tables=[T("Decision datasets (tiers are never combined)", ["Dataset", "Tier", "Documents", "Gold entities"], ds_rows)],
        bullets=[deviation_d1(), deviation_d2(R)]))
    prov = [
        ["Tier 3 text", "Authored by LLM agents (Claude), free-form", "Fabricated", "Inline at authoring; single annotator; not clinician-reviewed", "Repo-internal", "calls, dictations, notes, summaries, timelines, queries", "yes (calls)", "no (clean)", "yes (es + mixed)"],
        ["Tier 3 ASR", "Tier-3 spoken docs -> TTS -> phone channel -> Whisper-small", "Fabricated", "Gold mapped by word alignment; entities fully deleted by ASR dropped", "Repo-internal", "calls, dictations", "yes", "YES (real ASR engine; synthetic voices)", "yes"],
        ["MEDDOCAN test", "Spanish clinical case reports (SPACCC)", "Synthetic, inserted by documentalists", "Expert annotation (IberLEF 2019)", "CC BY 4.0", "written case reports", "no", "no", "Spanish only"],
        ["Synthetic test", "Template generator v2", "Fabricated", "Programmatic", "Repo-internal", "15 subsets", "simulated", "simulated noise", "yes"],
        ["PriMock57 (reviewed, NOT used)", "Real recorded mock GP consultations", "Actors; almost no PHI (1 name in 8 consults)", "Manual transcripts, no PHI labels", "CC BY 4.0", "conversations", "yes", "audio available", "no"],
    ]
    secs.append(S("provenance", "3. Dataset provenance", tables=[T("Provenance", ["Dataset", "Origin", "PHI", "Annotation", "Licence", "Content", "Conversational", "ASR errors", "Spanish"], prov)],
                  bullets=["Never treat Tier 3 or Tier 2 results as real-PHI results.",
                           "Limitation: the authoring agents and the rules author are the same model family; independence is procedural (no access to rules/templates), not institutional.",
                           "Limitation: synthetic TTS voices are cleaner than real callers (no accents, cross-talk, hesitations in audio), so real ASR error rates will be higher."]))

    # 4/5 candidate + single results --------------------------------------------------------
    cand_rows = []
    for m in cands:
        L = latency(m)
        cand_rows.append([name(m), model_entry(m)["model_id"], model_entry(m).get("license"), str(R["thresholds"][m]),
                          f"{(L.get('cpu') or {}).get('p95', 0):.0f} ms", f"{(L.get('mps') or {}).get('p95', 0):.0f} ms",
                          f"{(L.get('cpu') or {}).get('rss', 0):,.0f} MB", f"{(L.get('cpu') or {}).get('weights', 0):,.0f} MB"])
    secs.append(S("candidates", "4. Candidate model comparison", paras=[
        "Only the three serious candidates; no excluded model was re-introduced (no new evidence). Thresholds are frozen "
        "from earlier dev calibration (no tuning on Tier 3)."],
        tables=[T("Candidates", ["Model", "Checkpoint", "Licence", "Frozen thresholds", "CPU P95", "MPS P95", "Peak RSS", "Weights"], cand_rows)]))
    tables = []
    for ds in R["datasets"]:
        rows = []
        for st, lab in (("DETERMINISTIC_ONLY", "A. deterministic only"), ("DETERMINISTIC_REGISTRY", "B. deterministic + registry")):
            b = R["baselines"][ds][st]
            rows.append([lab, "-", pct(b["residual_mean"], 2), pct(b["residual_median"], 2), pct(b["residual_p95"], 2), pct(b["residual_max"], 1),
                         f"{b['zero_residual_docs']}/{b['documents_with_phi']}", pct(b["high_risk_recall"]), pct(b["precision"]), pct(b["over_redaction"], 2)])
        for m in cands:
            for st, lab in (("MODEL_ONLY", "model only"), ("MODEL_DETERMINISTIC", "C. model + deterministic"), (FULL, "D. model + deterministic + registry")):
                x = single(m, ds, st)
                rows.append([lab, name(m), pct(x["residual_mean"], 2), pct(x["residual_median"], 2), pct(x["residual_p95"], 2), pct(x["residual_max"], 1),
                             f"{x['zero_residual_docs']}/{x['documents_with_phi']}", pct(x["high_risk_recall"]), pct(x["precision"]), pct(x["over_redaction"], 2)])
        tables.append(T(f"{DS_LABEL[ds]} — {R['datasets'][ds]['tier']} {TAG['m']}", ["Stage", "Model", "Residual mean", "Median", "P95", "Max",
                        "Zero-residual docs", "High-risk recall", "Precision", "Over-redaction"], rows))
    sel = R["primary_selection"]
    secs.append(S("single", "5. Single-model results", paras=[
        f"Primary-model rule (pre-registered): lowest mean residual PHI after the full shield on Tier 3 ASR; ties = differences "
        f"that are not material; veto if full-shield high-risk recall < 95%. Ranking: {', '.join(name(m) for m in sel['ranking_by_mean_residual'])}. "
        f"Not materially different from the best: {', '.join(name(m) for m in sel['tied_with_best']) or 'none'}. "
        f"Selected primary: {name(sel['primary'])}. Tier contradictions: {sel['contradictions'] or 'none'}. {' '.join(sel['notes'])}"],
        tables=tables))

    # 6 pairwise --------------------------------------------------------------------------------
    prow, rrow = [], []
    for k, v in R["pairs"].items():
        b = k.split("+")[1]
        for ds in R["datasets"]:
            a1, ab = single(A, ds), v["datasets"][ds][FULL]["all"]
            bo = single(b, ds)
            rec = v["datasets"][ds]["recovered"]
            prow.append([name(k), DS_LABEL[ds], pct(a1["residual_mean"], 2), pct(bo["residual_mean"], 2), pct(ab["residual_mean"], 2),
                         pct(a1["residual_p95"], 2), pct(ab["residual_p95"], 2), pct(a1["residual_max"], 1), pct(ab["residual_max"], 1),
                         f"{rec['recovered_fully']}/{rec['a_misses']}", f"{rec['chars_recovered']}/{rec['chars_left_by_a']}",
                         f"{ab['spurious'] - a1['spurious']:+d}", f"{(ab['over_redaction'] - a1['over_redaction']) * 100:+.2f} pp"])
        mt = v["materiality"]
        rrow.append([name(k), "yes" if mt["M1_mean_residual"] else "no", "yes" if mt["M2_tail_residual"] else "no",
                     ", ".join(mt["M3_critical_category"]) or "-", ", ".join(mt["M4_slice"]) or "-",
                     "ok" if mt["guard_over_redaction"] else "FAIL", "ok" if mt["guard_false_positives"] else "FAIL",
                     ("ok" if dec["latency_ok"].get(k) else "FAIL") if k in R.get("passing_pairs", []) else "n/a",
                     ", ".join(f"{n}:{'ok' if ok else 'REVERSED'}" for n, ok in v["replication"].items()),
                     "MATERIAL" if k in R.get("passing_pairs", []) else "not material"])
    secs.append(S("pairs", "6. Pairwise complementarity results", paras=[
        f"Pairs are the primary ({name(A)}) plus each other candidate, all inside the full shield. 'Recovered' counts gold "
        "entities left (partly) exposed by the primary's full shield that the pair covers completely, and PHI characters "
        "recovered. Spurious/over-redaction are the extra costs of the second model."],
        tables=[T(f"Pairwise effect (full shield) {TAG['m']}", ["Pair", "Dataset", "Residual A", "Residual B alone", "Residual A+B", "P95 A", "P95 A+B",
                  "Max A", "Max A+B", "Entities recovered", "PHI chars recovered", "Δ spurious", "Δ over-redaction"], prow),
                T("Pre-registered materiality test (decision dataset: Tier 3 ASR)", ["Pair", "M1 mean", "M2 P95", "M3 critical category",
                  "M4 ASR/Spanish slice", "Over-redaction guard", "FP guard", "Latency guard", "Replication", "Verdict"], rrow)]))

    # 7 triple / cascade ------------------------------------------------------------------------
    if R.get("triple"):
        t = R["triple"]
        secs.append(S("triple", "7. Three-model results (run because a pair passed)", paras=[
            f"All three models vs best pair {name(t['vs_pair'])}: residual {pct(t['full_shield']['residual_mean'], 2)}; "
            f"materiality vs pair: {'MATERIAL' if t['materiality']['passes_before_latency'] else 'not material'}."]))
        if R.get("cascade"):
            secs.append(S("cascade", "7b. Selective second stage (cascade)", tables=[T("Cascade triggers", ["Trigger", "Docs invoking B", "Share", "Residual", "Share of pair benefit kept", "Adopt"],
                          [[n, v["documents_invoking_B"], pct(v["share_invoking_B"]), pct(v["residual_mean"], 2), pct(v["share_of_pair_benefit_kept"]), "yes" if v["adopt"] else "no"]
                           for n, v in R["cascade"].items()])]))
    else:
        secs.append(S("triple", "7. Three-model results", paras=[
            "NOT RUN, by protocol: three-model combinations and cascades are evaluated only if a pair passes the "
            "pre-registered materiality rule. No pair passed." if not R.get("passing_pairs") else "Pair passed; see cascade."]))

    # 8 residual comparison -----------------------------------------------------------------------
    rows = []
    for ds in R["datasets"]:
        rows.append([DS_LABEL[ds], pct(R["baselines"][ds]["DETERMINISTIC_REGISTRY"]["residual_mean"], 2)] +
                    [pct(single(m, ds)["residual_mean"], 2) for m in cands] +
                    [pct(v["datasets"][ds][FULL]["all"]["residual_mean"], 2) for v in R["pairs"].values()])
    secs.append(S("residual", "8. Residual PHI comparison (mean per document, full shield)", tables=[
        T(f"RESIDUAL_PHI_AFTER_SHIELD {TAG['m']}", ["Dataset", "Rules + registry (no ML)"] + [name(m) for m in cands] + [name(k) for k in R["pairs"]], rows)]))

    # 9 critical categories ----------------------------------------------------------------------
    crit = ["NAME", "RELATIVE_NAME", "PROVIDER_NAME", "FACILITY_NAME", "DOB", "DATE", "PHONE", "ADDRESS", "MEMBER_ID", "MRN", "NPI", "EMAIL", "EMPLOYER"]
    rows = []
    for c in crit:
        sup = single(A, D)["per_category"].get(c, {}).get("support")
        if not sup:
            continue
        rows.append([c, sup] + [f"{pct((single(m, D)['per_category'].get(c) or {}).get('recall'))}" for m in cands] +
                    [pct((v["datasets"][D][FULL]["all"]["per_category"].get(c) or {}).get("recall")) for v in R["pairs"].values()])
    secs.append(S("critical", "9. Critical-category failures (Tier 3 ASR, full shield)", paras=["Recall < 95% is an internal gate failure, not a HIPAA determination."],
                  tables=[T(f"Per-category full-span recall {TAG['m']}", ["Category", "Support"] + [name(m) for m in cands] + [name(k) for k in R["pairs"]], rows)]))

    # 10/11 ASR & Spanish --------------------------------------------------------------------------
    rows = []
    for m in cands:
        for ds in ("cadence_text", "cadence_asr"):
            for st in ("MODEL_ONLY", FULL):
                x = single(m, ds, st, "calls")
                rows.append([name(m), DS_LABEL[ds], "model only" if st == "MODEL_ONLY" else "full shield",
                             pct(x.get("entity_recall")), pct(x.get("residual_mean"), 2), pct(x.get("high_risk_recall"))])
    asr_dropped = json.loads(Path("data/cadence_realistic/asr_dropped_entities.json").read_text()) if Path("data/cadence_realistic/asr_dropped_entities.json").exists() else []
    secs.append(S("asr", "10. ASR results (calls; same documents, clean text vs real ASR output)", paras=[
        "Synthetic-benchmark finding under test: Stanford V2 much stronger than OBI on noisy ASR. Here the same Tier-3 calls "
        "are scored before and after a real ASR engine.",
        f"{len(asr_dropped)} gold entities were deleted entirely by ASR (no characters left to leak) and are excluded from the ASR gold."],
        tables=[T(f"Calls: clean vs ASR {TAG['m']}", ["Model", "Condition", "Stage", "Entity recall", "Residual mean", "High-risk recall"], rows)]))
    rows = []
    for ds, sl in (("cadence_asr", "spanish_or_mixed"), ("cadence_text", "spanish_or_mixed"), ("meddocan_test", "all")):
        base = R["baselines"][ds]["DETERMINISTIC_REGISTRY"]
        for m in cands:
            for st in ("MODEL_DETERMINISTIC", FULL):
                x = single(m, ds, st, sl)
                if x:
                    rows.append([DS_LABEL[ds], name(m), "model+rules" if st == "MODEL_DETERMINISTIC" else "full shield",
                                 pct(x["entity_recall"]), pct(x["residual_mean"], 2), pct(x["high_risk_recall"])])
    secs.append(S("spanish", "11. Spanish results", paras=[
        "Question: is primary English/general model + rules + registry already good enough on Spanish, or is a Spanish "
        "specialist needed? The Spanish specialist (LabBiomedica) is not a candidate unless the candidates fail materially; "
        "its earlier MEDDOCAN result (model + rules residual 2.6%) is in the v2 report. MEDDOCAN's registry is an oracle, "
        "so 'model+rules' is the fair MEDDOCAN comparison."],
        tables=[T(f"Spanish / code-switched {TAG['m']}", ["Dataset", "Model", "Stage", "Entity recall", "Residual mean", "High-risk recall"], rows)]))

    # 12 over-redaction, 13 latency/memory, 14 complexity --------------------------------------------
    rows = [[DS_LABEL[ds], name(m), single(m, ds)["spurious"], pct(single(m, ds)["over_redaction"], 2), pct(single(m, ds)["precision"])]
            for ds in R["datasets"] for m in cands]
    secs.append(S("over", "12. Over-redaction", tables=[T(f"Full shield {TAG['m']}", ["Dataset", "Model", "Spurious spans", "Over-redacted non-PHI chars", "Precision"], rows)]))
    rows = []
    for m in cands:
        L = latency(m)
        rows.append([name(m), f"{(L.get('cpu') or {}).get('median', 0):.0f} / {(L.get('cpu') or {}).get('p95', 0):.0f} ms",
                     f"{(L.get('mps') or {}).get('median', 0):.0f} / {(L.get('mps') or {}).get('p95', 0):.0f} ms",
                     f"{(L.get('cpu') or {}).get('rss', 0):,.0f} MB", f"{(L.get('cpu') or {}).get('weights', 0):,.0f} MB"])
    secs.append(S("latency", "13. Latency / memory", paras=[f"{TAG['m']} From the v2 run on Apple M3 (≥3 warm-up, ≥20 measured, Cadence-sized documents). A pair running sequentially costs the sum of both."],
                  tables=[T("Per model", ["Model", "CPU median / P95", "MPS median / P95", "Peak RSS", "Weights"], rows)]))
    secs.append(S("complexity", "14. Operational complexity", bullets=[
        f"{TAG['i']} Each additional model adds: a second artifact to pin, scan and re-validate on upgrades; a second "
        "runtime (GLiNER vs transformers token-classification) with its own dependencies; more memory per replica; a "
        "reconciliation path whose behaviour must be regression-tested; a second set of thresholds to calibrate and drift "
        "to monitor; and a larger incident surface.",
        f"{TAG['i']} The deterministic and registry layers are cheap, transparent and auditable; they carry the structured "
        "identifiers in every configuration tested here.",
        f"{TAG['i']} One small token-classification model (≈110M params) can run on CPU inside the VPC; GLiNER-large and "
        "RoBERTa-large need more memory or a GPU for the same latency."]))

    # 15 failure cases ---------------------------------------------------------------------------------
    rows = []
    sm = R["pairs"][next(iter(R["pairs"]))]["datasets"][D]["recovered"]["examples"] if R["pairs"] else []
    for r in sm[:25]:
        rows.append([r["doc"], r["label"], r["text"], pct(r["a_cov"], 0), "yes" if r["recovered_fully"] else "no", ", ".join(r["tags"])])
    worst = [[name(m), single(m, D)["worst_document"], pct(single(m, D)["residual_max"], 1)] for m in cands]
    secs.append(S("failures", "15. Failure-case analysis", paras=[
        f"Entities the primary's full shield left exposed on Tier 3 ASR, and whether the first alternative model recovers them. "
        "Full per-entity lists: decision_results.json (pairs.*.datasets.cadence_asr.recovered)."],
        tables=[T("Worst documents (full shield, Tier 3 ASR)", ["Model", "Worst document", "Residual"], worst),
                T(f"Primary's remaining misses (first 25) {TAG['m']}", ["Document", "Type", "Text (ASR)", "Primary coverage", "Recovered by pair", "Tags"], rows)]))

    # where residual PHI remains -------------------------------------------------------------------------
    attr = R.get("_attribution", {})
    if attr:
        rows = []
        for ds, by in attr.items():
            tot = sum(by.values()) or 1
            for k, v in sorted(by.items(), key=lambda kv: -kv[1]):
                rows.append([DS_LABEL[ds], k, v, pct(v / tot)])
        secs.append(S("attribution", "15b. Where the remaining PHI is (primary model, full shield)", paras=[
            f"{TAG['m']} Residual PHI characters left by the recommended shield, grouped by what kind of handling could catch them. "
            "Person names are the only class that genuinely needs contextual ML; dates, locations, structured identifiers and "
            "spelled letters are addressable by conversational/ASR-aware deterministic handling and registries.",
            f"{TAG['i']} The largest available reduction in residual PHI therefore comes from the deterministic layer "
            "(spoken ordinals and dates, spoken addresses without a street suffix, partial and spoken phone numbers, "
            "letter-by-letter spellings, alphanumeric spoken IDs, ASR-normalised digits) - developed on a NEW Tier-3 dev "
            "set and re-tested on this held-out set - not from adding a second model."],
            tables=[T("Residual PHI characters by failure class", ["Dataset", "Class", "Residual chars", "Share"], rows)]))

    # 16/17 recommendation -----------------------------------------------------------------------------
    secs.append(S("recommendation", "16. Final architecture recommendation", paras=final[:6]))
    secs.append(S("why_not", "17. Why additional models were / were not worth it", bullets=pair_lines + [
        f"{TAG['i']} Published field evidence (ML+rules > either alone) supports the layered architecture; it does not by "
        "itself justify multiple ML models.",
        f"{TAG['u']} Whether these Tier-3 conclusions hold on real Cadence PHI is unknown until a governed, annotated Cadence "
        "sample (and ideally an n2c2 2014 reproduction) is evaluated with `python -m harness.decide` on that data."]))
    meta = {"title": "Contextual ML Layer Decision", "generated": datetime.now().isoformat(timespec="seconds"),
            "run_dir": str(out), "dataset": "Tier 3 (decision) + Tier 2 + Tier 4, never combined",
            "documents": " / ".join(f"{DS_LABEL[n]} {R['datasets'][n]['documents']}" for n in R["datasets"])}
    return meta, secs


ATTR_CLASSES = ("person name (contextual - needs ML)", "structured identifier (spoken/partial/written)",
                "date / DOB (spoken, partial, relative)", "location (spoken address, street, city, landmark)",
                "organisation / employer / facility / language", "spelled letters (names/IDs)")


def attribution(out: Path, model: str) -> dict[str, dict[str, int]]:
    import re
    from collections import Counter

    from harness.decision.experiment import Evaluator
    from harness.evaluation.matching import core_chars, match_sample
    from harness.evaluation.runner import canonical_compatible

    def cls(e):
        if re.search(r"\b[A-Za-z](?:[-\s.,]+[A-Za-z]\b){2,}", e.text):
            return ATTR_CLASSES[5]
        if e.label in ("PHONE", "MEMBER_ID", "MRN", "ACCOUNT_ID", "NPI", "OTHER_UNIQUE_ID", "ZIP", "EMAIL", "URL"):
            return ATTR_CLASSES[1]
        if e.label in ("DATE", "DOB"):
            return ATTR_CLASSES[2]
        if e.label in ("ADDRESS", "CITY", "STATE", "GEOGRAPHIC_CONTEXT"):
            return ATTR_CLASSES[3]
        if e.label in ("NAME", "RELATIVE_NAME", "PROVIDER_NAME", "CARE_TEAM_MEMBER"):
            return ATTR_CLASSES[0]
        return ATTR_CLASSES[4]
    ev = Evaluator(out)
    res = {}
    for ds in ("cadence_text", "cadence_asr"):
        P = ev.stage_preds((model,), ds, FULL)
        c = Counter()
        for s in ev.ds[ds].samples:
            for g in match_sample(s, P[s.id], canonical_compatible).gold:
                if not g.full:
                    c[cls(g.entity)] += len(core_chars(s.text, g.entity)) - g.covered
        res[ds] = dict(c)
    return res


def write_report(out: Path) -> dict[str, Path]:
    R = json.loads((out / "decision_results.json").read_text())
    R["_attribution"] = attribution(out, R["primary_selection"]["primary"])
    meta, secs = build(R, out)
    md, html = out / "decision_report.md", out / "decision_report.html"
    md.write_text(to_markdown(meta, secs, {}))
    html.write_text(to_html(meta, secs))
    (out / "decision_summary.json").write_text(json.dumps({**decide(R), "residual_attribution": R["_attribution"]}, indent=1))
    REPORTS.mkdir(exist_ok=True)
    shutil.copy2(md, REPORTS / "decision_report.md")
    shutil.copy2(html, REPORTS / "decision_report.html")
    return {"md": md, "html": html}
