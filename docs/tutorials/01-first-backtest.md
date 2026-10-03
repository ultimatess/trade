# 01 — First Backtest (legacy synthetic simulation)

```bash
make backtest
```

This runs the **legacy** simulator: 500 synthetic one-minute candles. Read the result as a demonstration of the mechanics only:

- The generator draws the strategy's input features from the same regime label as the next price move, so the strategy is effectively shown the answer (docs/CURRENT_STATE.md §6).
- There is no out-of-sample split; the t-statistic covers all trades.
- Every seed tested "passes" with Sharpe 9–19. That is an artefact of the generator, not evidence of an edge.

The real backtester (point-in-time snapshots, simulated exchange, cost and slippage models, leakage tests) arrives in Phase 3. This tutorial will then be rewritten.
