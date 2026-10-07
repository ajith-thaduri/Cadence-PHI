"""Backward-compatible CLI entry point: python -m harness.phi.calibrate -> harness_eval.calibrate (evaluation layer)."""
if __name__ == "__main__":
    import sys

    from harness_eval.cli import calibrate_main as main
    sys.exit(main())
