"""Backward-compatible CLI entry point: python -m harness.phi.matrix -> harness_eval.matrix (evaluation layer)."""
if __name__ == "__main__":
    import sys

    from harness_eval.matrix import main
    sys.exit(main())
