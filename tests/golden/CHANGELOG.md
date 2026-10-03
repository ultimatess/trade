# Golden Fixture Changelog

Every regeneration of `tests/golden/fixtures/` is listed here with its defect ID and the measured before → after effect. Regenerate with `python -m tests.golden.characterize --write`; summarise with `python -m tests.golden.compare <before_dir>`.

The `reflex` and `risk` fixtures are pinned to the frozen legacy oracle and never change.

## D-001 — paper broker cash accounting (Phase 2, step 2.3)

**Defect:** cash was not debited on entry but was credited notional + P&L on exit, so cash and NAV inflated by the full notional on every trade. Because sizing uses `cash × fraction`, position sizes grew during a run.
**Fix:** debit the entry notional at fill; reject entries the cash cannot fund; NAV = cash + market value of open positions.
**Expected effect:** cash returns to `start + Σ net P&L`. Backtest P&L falls because positions stop growing.

### backtest
| run | total_trades | net_pnl | final_equity | hit_rate_pct | sharpe_ratio | max_drawdown_pct | t_statistic |
|---|---|---|---|---|---|---|---|
| seed123_n150 | 24 → 24 | 704.13 → 381.73 | 100704.13 → 100381.73 | 58.33 → 58.33 | 3.21 → 3.36 | 0.43 → 0.22 | 1.0 → 1.04 |
| seed123_n500 | 59 → 84 | 5002.1 → 2675.56 | 105002.1 → 102675.56 | 74.58 → 71.43 | 10.66 → 9.71 | 0.43 → 0.22 | 4.77 → 4.34 |
| seed1_n500 | 52 → 84 | 4254.1 → 4020.56 | 104254.1 → 104020.56 | 75.0 → 80.95 | 9.36 → 16.23 | 0.32 → 0.17 | 4.18 → 7.26 |
| seed2_n500 | 83 → 85 | 9372.15 → 4348.52 | 109372.15 → 104348.52 | 84.34 → 82.35 | 19.38 → 18.1 | 0.32 → 0.16 | 8.67 → 8.09 |
| seed3_n500 | 75 → 75 | 7987.67 → 3624.75 | 107987.67 → 103624.75 | 82.67 → 82.67 | 17.38 → 17.45 | 0.31 → 0.14 | 7.77 → 7.81 |
| seed42_n500 | 70 → 82 | 7268.24 → 3734.82 | 107268.24 → 103734.82 | 81.43 → 81.71 | 15.38 → 15.73 | 0.34 → 0.15 | 6.88 → 7.03 |

### broker
- flatten_dup_tiny: cash 119544.03 → 99935.08; equity 119544.03 → 99935.08; trades 2 → 2
- repeated_trades_cash_drift: cash 221131.90 → 101071.90; equity 221131.90 → 101071.90; trades 6 → 6
- stop_loss: cash 107191.60 → 99937.97; equity 107191.60 → 99937.97; trades 1 → 1
- take_profit: cash 120188.65 → 100178.65; equity 120188.65 → 100178.65; trades 1 → 1
- timeout: cash 109849.05 → 99990.92; equity 109849.05 → 99990.92; trades 1 → 1

### paper_cycle
- cash 108532.61 → 99990.94; equity 108532.61 → 99990.94; trades 1 → 1; brier 0.0 → 0.0

