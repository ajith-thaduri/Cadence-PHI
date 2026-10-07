"""python -m harness.evaluate

With --config: the Presidio PHI harness (harness/phi). Without: the original model benchmark."""
import sys

if __name__ == "__main__":
    if any(a == "--config" or a.startswith("--config=") for a in sys.argv[1:]):
        from harness_eval.cli import evaluate_main
        evaluate_main()
    else:
        from harness.cli.evaluate import main
        main()
