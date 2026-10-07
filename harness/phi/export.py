"""Backward-compatible CLI entry point: python -m harness.phi.export -> harness_eval.export (evaluation layer)."""
if __name__ == "__main__":
    import sys

    from harness_eval.export import main
    sys.exit(main())
