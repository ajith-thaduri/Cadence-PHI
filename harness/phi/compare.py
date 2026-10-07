"""Backward-compatible CLI entry point: python -m harness.phi.compare -> harness_eval.compare (evaluation layer)."""
if __name__ == "__main__":
    import sys

    from harness_eval.cli import compare_main as main
    sys.exit(main())
