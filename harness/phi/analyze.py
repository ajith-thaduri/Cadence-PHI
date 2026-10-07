"""Backward-compatible CLI entry point: python -m harness.phi.analyze -> harness_eval.analyze (evaluation layer)."""
if __name__ == "__main__":
    import sys

    from harness_eval.analyze import main
    sys.exit(main())
