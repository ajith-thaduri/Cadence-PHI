# Release gates (go / no-go)

```bash
python -m harness_eval.perf all --duration 3600 --device <serving device> --output results/perf/<rc>
python -m harness_eval.gates run --with-model --perf results/perf/<rc> [--release-eval results/phi/<T4 run>]
```

- **The decision is the minimum across gates.** Any FAIL or INSUFFICIENT_EVIDENCE means **NO-GO**.
- **Gate numbers live only in `configs/eval/gates.yaml`.** They are currently `status: proposed` and must be signed
  by the risk owner (plan §19 decision 1). They are never changed after seeing the results they gate.
- **Every run is recorded.** Each gate run is ledgered and writes `results/gates/<run>/gates.{json,md}`.

| gate | evidence | criterion (proposed) |
|---|---|---|
| G1 direct identifiers | T4 | 95% CI lower bound of per entity × modality recall ≥ 0.99 (typed / notes), ≥ 0.97 (ASR / telephone); ≥ 300 mentions, otherwise insufficient |
| G2 quasi identifiers | T4 | recall lower bound ≥ 0.95, with ≥ 100 mentions |
| G3 document sanitisation | T4 | direct-identifier document sanitisation lower bound ≥ 0.99, with ≥ 300 documents |
| G4 regression | T1, T2 | 0 new misses against the reviewed baselines (`baseline_fast.json`, `baseline.json`) |
| G5 behavioural | T1 | CheckList MFT 100% for every enabled entity (fast and model tiers) |
| G6 utility | T4 | over-redaction ≤ 3% of non-PHI characters; 0 must-not-redact violations |
| G7 fail-closed and security | T5, T6 | 100% of the listed test files pass |
| G8 performance | T7 | typical (≤4000-character) call: p95 ≤ 500 ms and p99 ≤ 1500 ms (CPU / MPS); soak ≥ 1 h with RSS growth ≤ 50 MB/h; deterministic |
| G9 operations | T8 | monitoring baseline exported, canaries configured and passing, system card generated |

## Status: release candidate 2026-10-06 (`results/gates/rc_2026-10-06`)

| gate | verdict | why |
|---|---|---|
| G1, G2, G3, G6 | INSUFFICIENT_EVIDENCE | No governed real-data (T4) evaluation exists. Fabricated data is rejected by design. |
| G4 | PASS | 0 regressions on the fast and model tiers |
| G5 | PASS | fast 1319/1319; model 62/62 |
| G7 | PASS | fail-closed, telemetry, boundary, vault, outbound-guard, shadow and monitoring tests |
| G8 | **FAIL** | CPU p95 for a typical call is 1186 ms (OBI RoBERTa-large on 128-token windows). MPS p95 is 560 ms. The rules-only tier is 15 ms. |
| G9 | PASS | baseline, canaries (4/4) and system card present |

**What would move this to GO:**

1. A governed T4 evaluation, meeting G1–G3 and G6.
2. A serving device or model configuration that meets G8, plus a one-hour soak on that device. Options include a GPU, a
   distilled model, or the long-call window strategy. Any model change must go back through G4/G5 and a dev re-calibration.
3. Sign-off on the gate numbers.
