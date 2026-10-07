"""Derived human-readable reports (HTML + Markdown) for one evaluation run. No PHI values."""
from __future__ import annotations

import html
from pathlib import Path
from typing import Any, Optional

CSS = """
:root{--bg:#fff;--fg:#1d2330;--mute:#5b6475;--line:#dfe3ea;--bad:#b3261e;--hd:#f4f6f9}
@media (prefers-color-scheme: dark){:root{--bg:#14171c;--fg:#e6e9ef;--mute:#9aa3b2;--line:#2c323c;--bad:#ff7b72;--hd:#1c2027}}
body{background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,-apple-system,sans-serif;margin:0 auto;max-width:1200px;padding:16px}
h1{font-size:22px}h2{font-size:17px;margin-top:28px;border-bottom:1px solid var(--line);padding-bottom:4px}
table{border-collapse:collapse;width:100%;margin:8px 0;font-size:12.5px;display:block;overflow-x:auto}
th,td{border:1px solid var(--line);padding:3px 6px;text-align:right;white-space:nowrap}th{background:var(--hd)}
td:first-child,th:first-child{text-align:left}.bad{color:var(--bad);font-weight:600}.note{color:var(--mute);font-size:12.5px}
"""


def pct(v: Optional[float], d: int = 1) -> str:
    return "–" if v is None or v == "" else f"{float(v) * 100:.{d}f}%"


def num(v: Any, d: int = 2) -> str:
    return "–" if v is None or v == "" else (f"{v:.{d}f}" if isinstance(v, float) else str(v))


def _summary_rows(summary: dict) -> tuple[list[str], list[list[str]]]:
    head = ["dataset", "docs", "residual mean", "median", "P95", "corpus residual", "docs sanitised", "high-risk full recall",
            "entity full recall", "span precision", "spurious", "over-redaction", "ASR-lost entities"]
    rows = []
    for n, d in summary["datasets"].items():
        r = d["RESIDUAL_PHI_AFTER_SHIELD"]
        rows.append([n, str(d["documents"]), pct(r["mean"]), pct(r["median"]), pct(r["p95"]), pct(r["corpus_residual_char_rate"]),
                     pct(d["complete_document_sanitization_rate"]), pct(d["high_risk_full_recall"]), pct(d["entity_full_recall"]),
                     pct(d["span_precision"]), str(d["spurious_spans"]), pct(d["over_redaction_char_rate"], 2),
                     str(d.get("asr_lost_entities", 0))])
    return head, rows


ENT_HEAD = ["entity", "support", "TP", "FN", "FP", "precision", "recall", "full-span recall", "char recall", "residual chars",
            "mean score TP", "mean score FP", "best rejected (FN)", "model thresholds"]


def _ent_cells(x: dict) -> list[str]:
    return [x["entity"], str(x["support"]), str(x["tp"]), str(x["fn"]), str(x["fp"]), pct(x["precision"]), pct(x["recall"]),
            pct(x["full_span_recall"]), pct(x["char_recall"]), str(x["residual_chars"]), num(x["mean_score_tp"]),
            num(x["mean_score_fp"]), num(x["mean_best_rejected_score_fn"]), x["model_thresholds"]]


def html_table(head: list[str], rows: list[list[str]]) -> str:
    h = "".join(f"<th>{html.escape(x)}</th>" for x in head)
    b = "".join("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>"


def md_table(head: list[str], rows: list[list[str]]) -> str:
    return "\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(r) + " |" for r in rows])


def write_reports(out: Path, summary: dict, results: list[dict], curves: Optional[list[dict]] = None,
                  extra: Optional[list[tuple[str, list[str], list[list[str]], str]]] = None,
                  title: str = "PHI engine evaluation") -> None:
    cfg = summary["config"]
    intro = (f"Config {summary.get('config_path')} (sha {cfg['config_sha256']}); models: {', '.join(cfg['models_enabled']) or 'none'}; "
             f"normalizers: {', '.join(cfg['normalizers_enabled']) or 'none'}; threshold overlays: {cfg['threshold_overlays'] or 'none'}. "
             "Scores are raw recognizer scores in their own scale (score_type), not probabilities. No PHI values appear in this report.")
    sections: list[tuple[str, list[str], list[list[str]], str]] = [("Safety summary", *_summary_rows(summary), "")]
    for r in results:
        sections.append((f"Entity metrics — {r['dataset']}", ENT_HEAD,
                         [_ent_cells(x) for x in r["entity_rows"] if x["support"] or x["predicted_spans"]], ""))
        bd = summary["datasets"][r["dataset"]]["breakdown"]
        att = bd["attribution"]
        keys = sorted({k for v in att.values() for k in v})
        sections.append((f"Outcome attribution per gold entity — {r['dataset']}", ["entity"] + keys,
                         [[e] + [str(v.get(k, 0)) for k in keys] for e, v in sorted(att.items())],
                         "detected_by_* = fully redacted, attributed to the evidence sources that accepted it."))
        sections.append((f"Where the residual PHI is — {r['dataset']}", ["dimension", "top contributors (share of leaked chars)"],
                         [[k, ", ".join(f"{a}: {pct(c['share'])}" for a, c in list(bd[k].items())[:8])]
                          for k in ("entity", "outcome", "language", "doc_type", "asr_altered")], ""))
    for s in extra or []:
        sections.append(s)
    body = "".join(f"<h2>{html.escape(t)}</h2>{'<p class=note>' + html.escape(n) + '</p>' if n else ''}{html_table(h, rows)}"
                   for t, h, rows, n in sections)
    (out / "report.html").write_text(
        f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body><h1>{html.escape(title)}</h1>"
        f"<p class=note>{html.escape(intro)}</p>{body}</body></html>", encoding="utf-8")
    md = [f"# {title}", "", intro, ""]
    for t, h, rows, n in sections:
        md += [f"## {t}", ""] + ([n, ""] if n else []) + [md_table(h, rows), ""]
    (out / "report.md").write_text("\n".join(md), encoding="utf-8")


# ---------------------------------------------------------------------------- prompt-suite report
def write_suite_report(out: Path, records: list[dict], summ: dict, cfg_obj: Any, hidden_holdout: bool = True) -> None:
    """Suite report in the evaluation-plan structure: overall, by entity, by subtype, by group, by detection
    method, by failure reason / stage, missed and unexpected entities, per-prompt records."""
    d = cfg_obj.describe()
    intro = (f"Config sha {d['config_sha256']} · environment {d['environment']} · catalog {d['catalog_version']} · models "
             f"{', '.join(d['models_enabled']) or 'none'} · {summ['prompts']} prompts ({summ['prompts_holdout']} holdout"
             f"{' - per-prompt details hidden' if hidden_holdout else ''}). Scores are raw recognizer scores (not probabilities). "
             "This report contains fabricated suite text.")
    agg_head = ["key", "expected", "detected", "redacted", "missed", "full recall", "residual chars"]
    agg = lambda m: [[k, str(v["expected"]), str(v["detected"]), str(v["redacted"]), str(v["missed"]), pct(v["recall_full"]),
                      str(v["residual_chars"])] for k, v in m.items()]
    overall = [["prompts", str(summ["prompts"])], ["prompts passed (all PHI redacted, no FP)", str(summ["prompts_passed"])],
               ["expected entities", str(summ["expected_entities"])], ["detected (full span)", str(summ["detected"])],
               ["redacted correctly", str(summ["redacted"])], ["missed", str(summ["missed"])],
               ["false positives (unexpected redactions)", str(summ["false_positives"])],
               ["negative prompts with any redaction", str(summ["negative_prompts_with_redaction"])],
               ["must-not-redact violations", str(summ["must_not_redact_violations"])],
               ["expected PHI chars", str(summ["expected_phi_chars"])], ["residual PHI chars", str(summ["residual_phi_chars"])],
               ["residual PHI %", pct(summ["residual_phi_pct"], 2)], ["over-redacted chars", str(summ["over_redacted_chars"])],
               ["holdout full recall (aggregate only)", pct(summ["holdout_view"]["recall_full"])]]
    method = [[k, str(v["contributed"]), str(v["sole_catch"]), str(v["false_positives"])]
              for k, v in sorted(summ["by_detection_method"].items())]
    reasons = [[k, str(v)] for k, v in sorted(summ["by_failure_reason"].items(), key=lambda kv: -kv[1])]
    stages = [[str(k), str(v)] for k, v in sorted(summ["by_failed_stage"].items(), key=lambda kv: -kv[1])]
    missed = []
    for r in records:
        for e in r["missed"]:
            br = e.get("best_rejected") or {}
            missed.append([r["prompt_id"], e["entity_type"], e.get("subtype") or "", repr(e.get("text") or ""), e["outcome"],
                           str(e.get("failed_stage")),
                           (f"{br.get('recognizer')} {br.get('raw_score')} vs thr {br.get('threshold')} ({br.get('reason')})" if br else "–"),
                           ",".join(e.get("expected_sources") or [])])
    unexpected = [[r["prompt_id"], f["entity"], f"{f['start']}-{f['end']}", repr(r["input_text"][f["start"]:f["end"]]),
                   f["recognizers"], str(f["decision_score"]), f["reason"]] for r in records for f in r["unexpected"]]
    per_prompt = [[r["prompt_id"], r["group"], "PASS" if r["pass"] else "FAIL", str(r["expected_entities"]), str(r["detected"]),
                   str(r["redacted"]), str(len(r["missed"])), str(len(r["unexpected"])),
                   f"{r['residual_chars']}/{r['core_chars']}"] for r in records]
    sections = [("Overall", ["metric", "value"], overall, ""),
                ("By entity", agg_head, agg(summ["by_entity"]), ""),
                ("By subtype", agg_head, agg(summ["by_subtype"]), ""),
                ("By prompt group", agg_head, agg(summ["by_group"]), ""),
                ("By detection method", ["source group", "entities contributed to", "sole catches", "false positives"], method,
                 "Sole catch = the only source group that protected the entity (it would leak without it)."),
                ("By failure reason", ["failure reason", "entities"], reasons, ""),
                ("By failed stage", ["stage", "entities"], stages, ""),
                ("Missed entities (why)", ["prompt", "entity", "subtype", "text", "failure reason", "stage",
                                           "best rejected evidence", "expected sources"], missed, ""),
                ("Unexpected detections (false positives)", ["prompt", "entity", "span", "text", "recognizers", "score", "reason"],
                 unexpected, ""),
                ("Per prompt", ["prompt", "group", "result", "expected", "detected", "redacted", "missed", "FP",
                                "residual/core chars"], per_prompt, "")]
    body = "".join(f"<h2>{html.escape(t)}</h2>{'<p class=note>' + html.escape(n) + '</p>' if n else ''}{html_table(h, r)}"
                   for t, h, r, n in sections)
    (out / "report.html").write_text(
        f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>PHI Prompt Suite</title><style>{CSS}</style></head><body><h1>PHI prompt suite — english_v1</h1>"
        f"<p class=note>{html.escape(intro)}</p>{body}</body></html>", encoding="utf-8")
    md = ["# PHI prompt suite — english_v1", "", intro, ""]
    for t, h, r, n in sections:
        md += [f"## {t}", ""] + ([n, ""] if n else []) + [md_table(h, r) if r else "_none_", ""]
    (out / "report.md").write_text("\n".join(md), encoding="utf-8")
