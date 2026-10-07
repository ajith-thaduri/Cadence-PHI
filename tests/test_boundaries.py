"""Architecture boundaries (plan §3): the runtime engine must never import the evaluation layer, so gold
labels, datasets, gates and evaluation configuration cannot reach the production code path."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "harness" / "phi"
CLI_SHIMS = {"analyze.py", "calibrate.py", "compare.py", "export.py", "matrix.py"}   # entry points only


def _imports(p: Path) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(p.read_text())):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


def test_runtime_never_imports_evaluation_layer():
    bad = []
    for p in RUNTIME.rglob("*.py"):
        if p.parent == RUNTIME and p.name in CLI_SHIMS:
            continue
        for m in _imports(p):
            if m.startswith(("harness_eval", "harness_ui")) or m.startswith("harness.evaluation") or m.startswith("harness.decision") \
                    or m.startswith("harness.benchmarking") or m.startswith("harness.reporting"):
                bad.append(f"{p.relative_to(ROOT)} imports {m}")
    assert not bad, bad


def test_cli_shims_are_entry_points_only():
    for name in CLI_SHIMS:
        tree = ast.parse((RUNTIME / name).read_text())
        top = [n for n in tree.body if not isinstance(n, (ast.Expr, ast.If))]
        assert not top, f"{name} must only contain a docstring and an `if __name__ == '__main__'` block"


def test_runtime_does_not_read_evaluation_config():
    for p in RUNTIME.rglob("*.py"):
        if p.parent == RUNTIME and p.name in CLI_SHIMS:
            continue
        txt = p.read_text()
        assert "gates.yaml" not in txt and "datasets.yaml" not in txt or p.name == "loader.py", p
