"""Static PNG charts for the v2 report (matplotlib, Agg).

Colours: validated categorical palette slots in fixed registry order (colour follows the
model). >4 series use slots 5-8. Every chart has a companion table in the report.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from harness.reporting.analysis import FULL, g, ok, stage_metric  # noqa: E402
from harness.reporting.loader import display_name  # noqa: E402

SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948", "#52514e"]
TEXT, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#e6e5e0", "#fcfcfb"


def _style(ax, title, xlabel="", ylabel=""):
    ax.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", fontsize=11, color=TEXT, pad=10)
    ax.set_xlabel(xlabel, color=MUTED, fontsize=9)
    ax.set_ylabel(ylabel, color=MUTED, fontsize=9)
    ax.tick_params(colors=MUTED, labelsize=8, length=0)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.grid(True, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def _fig(w=9.5, h=4.6):
    fig, ax = plt.subplots(figsize=(w, h), dpi=130)
    fig.patch.set_facecolor(SURFACE)
    return fig, ax


def _save(fig, path):
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    return path


def _bars(run, out, cats, getter, title, fname, ylabel="Full-span recall", gate=0.95):
    M = ok(run)
    fig, ax = _fig(10.5, 4.8)
    n = len(M)
    w = 0.84 / max(n, 1)
    x = np.arange(len(cats))
    for i, (k, r) in enumerate(M.items()):
        vals = [getter(r, c) or 0 for c in cats]
        ax.bar(x + (i - (n - 1) / 2) * w, vals, w * 0.9, color=SERIES[i % len(SERIES)], label=display_name(k),
               edgecolor=SURFACE, linewidth=0.8)
    ax.set_xticks(x, cats, rotation=25, ha="right")
    ax.set_ylim(0, 1.05)
    if gate:
        ax.axhline(gate, color=MUTED, linewidth=0.8, linestyle="--")
    _style(ax, title, "", ylabel)
    ax.legend(frameon=False, fontsize=7, ncol=5, loc="upper center", bbox_to_anchor=(0.5, -0.28))
    return _save(fig, out / fname)


def make_all(run, out: Path) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    res = {}
    cats = ["NAME", "PROVIDER_NAME", "FACILITY_NAME", "DOB", "DATE", "PHONE", "ADDRESS", "MEMBER_ID", "MRN", "NPI"]
    res["category_recall"] = _bars(run, out, cats, lambda r, c: stage_metric(r, "synthetic_test", "MODEL_ONLY_DEFAULT", "metrics", "per_category", c, "recall_full"),
                                   "High-risk category recall, model only [synthetic test]", "category_recall.png")
    stages = ["MODEL_ONLY", "MODEL_DETERMINISTIC", FULL]
    res["stages"] = _bars(run, out, ["synthetic: model", "synthetic: +rules", "synthetic: +rules+registry",
                                     "MEDDOCAN: model", "MEDDOCAN: +rules", "MEDDOCAN: +rules+registry"],
                          lambda r, c: stage_metric(r, "synthetic_test" if c.startswith("synthetic") else "meddocan_test",
                                                    stages[["model", "+rules", "+rules+registry"].index(c.split(": ")[1])],
                                                    "metrics", "high_risk_recall"),
                          "High-risk recall by shield stage (separate test sets, never combined)", "stages.png")
    res["residual"] = _bars(run, out, ["synthetic: model", "synthetic: full shield", "MEDDOCAN: model", "MEDDOCAN: full shield"],
                            lambda r, c: stage_metric(r, "synthetic_test" if c.startswith("synthetic") else "meddocan_test",
                                                      "MODEL_ONLY" if c.endswith("model") else FULL, "safety", "RESIDUAL_PHI_AFTER_SHIELD", "mean"),
                            "Mean RESIDUAL_PHI_AFTER_SHIELD per document (lower is better)", "residual.png", "Residual PHI fraction", gate=None)
    res["noise"] = _bars(run, out, ["clean", "low", "medium", "high"],
                         lambda r, c: stage_metric(r, "synthetic_test", "MODEL_ONLY_DEFAULT", "metrics", "by_noise_level", c, "recall_full"),
                         "Recall by ASR noise level, model only [synthetic test]", "noise.png")
    # long docs
    fig, ax = _fig(9.5, 4.6)
    for i, (k, r) in enumerate(ok(run).items()):
        for strat, ls in (("production", "-"), ("truncate", ":")):
            rows = [x for x in r.get("long_documents", []) if x["strategy"] == strat]
            sizes = sorted({x["target_tokens"] for x in rows})
            if not sizes:
                continue
            vals = [np.mean([x["recall_full"] for x in rows if x["target_tokens"] == s]) for s in sizes]
            ax.plot(sizes, vals, color=SERIES[i % len(SERIES)], linestyle=ls, linewidth=1.8, marker="o", markersize=3.5,
                    label=f"{display_name(k)} - {'production chunking' if strat == 'production' else 'no chunking'}")
    ax.set_xscale("log", base=2)
    ax.set_xticks([500, 1000, 2000, 4000, 8000, 16000], ["500", "1k", "2k", "4k", "8k", "16k"])
    ax.set_ylim(0, 1.05)
    _style(ax, "Long-document recall: production chunking (solid) vs single pass (dotted)", "target tokens", "Full-span recall")
    ax.legend(frameon=False, fontsize=6, ncol=3, loc="lower left")
    res["long"] = _save(fig, out / "long_documents.png")
    # latency
    keys = [k for k in ok(run) if g(run["latency"].get(k), "single_doc")]
    if keys:
        fig, ax = _fig(9.5, 0.5 * len(keys) + 1.5)
        y = np.arange(len(keys))
        mps = [run["latency"][k]["single_doc"]["p95_ms"] for k in keys]
        cpu = [g(run["latency_cpu"].get(k), "single_doc", "p95_ms") or 0 for k in keys]
        ax.barh(y - 0.2, mps, 0.38, color=SERIES[0], label="MPS P95", edgecolor=SURFACE)
        ax.barh(y + 0.2, cpu, 0.38, color=SERIES[1], label="CPU P95", edgecolor=SURFACE)
        ax.set_yticks(y, [display_name(k) for k in keys])
        ax.invert_yaxis()
        ax.set_xscale("log")
        _style(ax, "Warm single-document P95 latency (log scale)", "milliseconds")
        ax.legend(frameon=False, fontsize=8)
        res["latency"] = _save(fig, out / "latency.png")
    # PR on synthetic test (filterable models)
    fig, ax = _fig(7.5, 4.6)
    for i, (k, r) in enumerate(ok(run).items()):
        rows = [x for x in (g(r, "pairs", "synthetic_test", "test_sweep") or []) if x["threshold"] is not None and x["precision"] is not None]
        if rows:
            ax.plot([x["recall_full"] for x in rows], [x["precision"] for x in rows], color=SERIES[i % len(SERIES)],
                    linewidth=1.8, marker="o", markersize=3.5, label=display_name(k))
    _style(ax, "Precision vs full-span recall across thresholds, model only [synthetic test; reporting only]", "recall", "precision")
    ax.legend(frameon=False, fontsize=7)
    res["pr"] = _save(fig, out / "pr_curves.png")
    res["failures"] = _bars(run, out, ["model_missed_entity", "wrong_entity_boundary", "context_failure", "asr_failure",
                                       "identifier_format_failure", "spanish_failure", "long_document_failure"],
                            lambda r, c: (g(r, "pairs", "synthetic_test", "model_only_failure_counts", c, default=0) /
                                          max(1, sum((g(r, "pairs", "synthetic_test", "model_only_failure_counts") or {}).values()))),
                            "Share of model-only false negatives by primary failure category [synthetic test]", "failures.png",
                            "share of FNs", gate=None)
    return res
