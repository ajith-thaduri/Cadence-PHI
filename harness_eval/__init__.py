"""Evaluation layer for the PHI engine (TEVV). NEVER imported by the runtime engine (harness/phi):
gold labels, datasets, gates and evaluation configuration cannot reach the production code path.
An import-boundary test (tests/test_boundaries.py) enforces this."""
