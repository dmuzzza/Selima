# Reference report: `rejected` on a known-weak strategy

Reference output of `scripts/evaluate.py` with `target_edge_bps = 25`, produced
with `--dry-run` on 2026-11-09. The journal and the quarterly budget were not
touched: the run exists to show how the command behaves, not to test an idea.

Why this report matters: `dual_ma_crossover_after_testing` on the 20-ticker 4h
pool has a pool CI95 upper bound of +16.52 bps, below the 25 bps target, so the
verdict is `rejected` — the system closes a known-weak strategy instead of
answering "not enough data". That is the calibration case for the `rejected`
branch; no idea slot is spent on it.

Reproduce:

```bash
uv run python scripts/evaluate.py \
    --strategy dual_ma_crossover_after_testing \
    --hypothesis "reference: dual_ma must be rejected at the 25 bps target" \
    --dry-run --tag reference-rejected
```

Numbers to compare after a model change: pool 1698 trades (+13 belong to the
sealed period, `scripts/verdict.py` shows 1711), development 402 trades,
sigma 286 bps, MDE 19.4 bps at one trial and 29.6 bps at 80 trials.

---

# Evaluate: dual_ma_crossover_after_testing / 4h
Hypothesis: reference: dual_ma must be rejected at the 25 bps target
Ideas this quarter: 0 of 20 (this run adds 0, grid_cells 1)

> **NOT RECORDED (dry run):** the journal and the budget were not touched.

## Verdict: rejected
Reason: pool interval is below the target: upper bound +16.52 bps < 25 bps

## Numbers

```
fit (screening)     1295 trades    +8.48 bps  CI80 [-1.29; +18.27]
pool (before sealed)  1698 trades    +2.58 bps  CI95 [-10.68; +16.52]
development          402 trades   -13.29 bps  CI95 [-42.45; +20.23]
costs x2            1698 trades    -6.54 bps  CI95 [-19.92; +7.56]
TP penalty          1698 trades    +0.64 bps  CI95 [-12.55; +14.44]
without best        1613 trades    -0.27 bps  CI95 [-14.17; +14.28]
sealed OOS        not accessed (starts 2026-10-01)
```

note: the pool stops before 2026-10-01 (1260 bars cut). `scripts/verdict.py` runs the full history, so its pool is larger by the sealed part; compare pools only within one tool or after the sealed period is opened.

## Power

sigma 286 bps | MDE 19 bps | MDE with 80 trials 30 bps | target 25 bps | power 0.80
trades needed for target: ~2377 trades

## Checks

null ok | plateau no_plateau (no plateau) | tradable True | base verdict inconclusive
dividend coverage: applied=123, no_gap_match=6, future=4, out_of_history=20, not_paid=1, unconfirmed tickers=12

## Assumptions

- T2-17a: the broker's charge for a short held through an ex-dividend date is not verified; the model assumes the short pays the gross dividend.

## What would change the verdict

- the pool must reach ~2377 trades for a 25 bps target at 80 trials
- the sealed OOS must exclude zero at alpha 0.05/k after screening
- x2 costs must keep the lower bound above zero

## Biases

The 20-ticker universe was chosen by today's liquidity: survivorship bias. Manual --tickers runs are recorded separately and count as a new trial.

portfolio 1+d614b83be1d1 | evaluate 7+78d132a51a37
