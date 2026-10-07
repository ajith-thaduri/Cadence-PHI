"""Markdown and HTML renderers for the shared section model."""
from __future__ import annotations

import base64
import html
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES = Path(__file__).parent / "templates"


def _md_cell(v) -> str:
    return str("" if v is None else v).replace("|", "\\|").replace("\n", " ")


def to_markdown(meta, sections, image_rel: dict[str, str]) -> str:
    out = [f"# {meta['title']}", "",
           f"Generated {meta['generated']} - dataset `{meta['dataset']}` ({meta['documents']} documents) - "
           f"run `{meta['run_dir']}`", "",
           "Evidence tags on every table: **[PUBLISHED REAL DATA]** (others' results), **[OUR SYNTHETIC BENCHMARK]**, "
           "**[OUR PUBLIC-CORPUS TEST — MEDDOCAN]** (real case text, synthetic PHI), **[ENGINEERING INFERENCE]**, **[UNKNOWN]**. "
           "No result is labelled [OUR REAL-DATA REPRODUCTION]: none was possible. Synthetic and real results are never combined.", ""]
    for s in sections:
        out += [f"## {s['title']}", ""]
        for p in s["paras"]:
            out += [p, ""]
        for b in s["bullets"]:
            out.append(f"- {b}")
        if s["bullets"]:
            out.append("")
        for t in s["tables"]:
            if not t["rows"]:
                out += [f"*{t['title']}: no rows.*", ""]
                continue
            out += [f"**{t['title']}**", "", "| " + " | ".join(_md_cell(c) for c in t["columns"]) + " |",
                    "|" + "---|" * len(t["columns"])]
            out += ["| " + " | ".join(_md_cell(c) for c in r) + " |" for r in t["rows"]]
            out.append("")
        for img in s["images"]:
            if img and img in image_rel:
                out += [f"![{Path(img).stem}]({image_rel[img]})", ""]
    return "\n".join(out)


def to_html(meta, sections) -> str:
    def img64(p):
        if not p or not Path(p).exists():
            return None
        return "data:image/png;base64," + base64.b64encode(Path(p).read_bytes()).decode()

    for s in sections:
        s["images_b64"] = [x for x in (img64(p) for p in s["images"]) if x]
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html", "j2"]))
    return env.get_template("report.html.j2").render(meta=meta, sections=sections, esc=html.escape)
