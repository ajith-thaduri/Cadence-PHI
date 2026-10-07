"""Operator console for the PHI engine: a FastAPI service + a React single-page app (frontend/).

Privacy design (docs/ui_guide.md):
- the API never echoes original text: responses carry offsets, decisions and the SANITIZED text; the browser
  highlights the text it already holds
- nothing but sanitized output and offsets is written to disk; logs and the audit trail carry ids, counts and
  keyed hashes only
- all assets are served locally (no CDN), strict Content-Security-Policy, no CORS
- evaluation work runs through an allow-list of reproducible CLI commands; the exact command is shown

The runtime engine (harness.phi) never imports this package.
"""
__version__ = "1.0.0"
