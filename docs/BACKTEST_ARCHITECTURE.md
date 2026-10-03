# Backtest Architecture

## 1. Event-Driven Loop

```
HistoricalDataProvider ─► EventQueue (ordered by available_at, then sequence)
      │
      ▼
SimulatedClock.advance(event.available_at)
      │
      ├─ MarketEvent ─► SimulatedExchange.on_market(event)   # match resting orders FIRST
      │                 └─► Fill events ─► Portfolio ─► Ledger ─► Strategy.on_fill
      │
      └─ SnapshotBuilder.build(symbol, as_of=clock.now) ─► Strategy.on_snapshot ─► Signals
                       ─► Sizer ─► OrderIntent ─► RiskEngine.evaluate ─► (ALLOW) ─► SimulatedExchange.submit
                                                                       └─ (DENY)  ─► Ledger
      │
      ▼
end of data ─► session flatten ─► Metrics ─► BacktestRun record + artifacts
```

Properties:
- **Each symbol has its own price series.** The current shared-series bug cannot happen, because events are keyed by symbol.
- Orders submitted at time T can fill no earlier than `T + latency`, against market events whose `event_time ≥ T + latency`. **The signal price is never the fill price.**
- RiskEngine, Sizer, Portfolio and Ledger are the production classes, not backtest copies.
- The run is deterministic: `(dataset@version, strategy@version+hash, params, cost_model@version, slippage_model@version, latency_model, seed, code_commit)` gives a byte-identical trade list. A test enforces this.

## 2. SimulatedExchange

| Feature | Model |
|---|---|
| Market order | Fills at next available quote: ask for buy, bid for sell; plus slippage model |
| Limit order | Fills if the market trades through the limit. At-touch fills are treated as queue-uncertain and need `fill_at_touch_prob` (default 0, conservative) |
| Stop / stop-limit | Triggers on a trade or bar through the stop. **Gaps fill at the worse of stop and open** |
| Partial fills | Participation cap: fill ≤ `max_participation × bar_volume` (default 10%); the remainder rests or cancels per time-in-force |
| Latency | Fixed or distribution, seeded |
| Rejections | Price-band (circuit) rejections; configurable random reject rate for stress tests; insufficient-funds rejections |
| Liquidity | Orders larger than displayed depth walk the book when depth data exists |
| Intrabar ambiguity | If one bar's range contains both TP and SL, **assume the stop fills first** unless tick data resolves it |
| Session | No fills outside session; forced flatten at `flatten_at` with closing-auction or last-price policy |
| Brackets | OCO implemented as linked orders in the exchange simulator, not by checking P&L |

## 3. Cost & Slippage Models

```yaml
# config/cost_models/nse_intraday.yaml
id: nse_intraday
version: 1
effective_from: "2024-10-01"      # operator must verify against current NSE/SEBI circulars
currency: INR
brokerage: {per_order_flat: 20.0, per_order_pct: 0.0003, rule: min}   # current code behaviour
stt: {sell_pct: 0.00025}
exchange_txn: {pct: 0.0000297}
sebi: {pct: 0.000001}
stamp_duty: {buy_pct: 0.00003}
gst: {pct: 0.18, on: [brokerage, exchange_txn, sebi]}
```

- `CostModel` is a generic interpreter over this schema. The NSE intraday model reproduces `IndianTaxCalculator` exactly; a parity test checks this. `nse_delivery` and `us_equities` are further configs.
- `SlippageModel`: `fixed_bps`, `spread_fraction`, `volatility_scaled`, `square_root_impact`. The default for 1-minute NSE is `spread_fraction=0.5 + volatility_scaled`.

## 4. Metrics (`trade.research.validation.metrics`)

Computed from the **daily marked-to-market equity curve** and the trade list. Per-trade returns are not used as if they were periods.

Return: total return, CAGR, monthly/daily returns tables. Risk-adjusted: Sharpe (daily, annualised √252), Sortino, Calmar. Drawdown: max drawdown, duration, recovery time, underwater curve. Trade: count, win rate, profit factor, expectancy, average/largest win and loss, max consecutive losses, average holding time. Activity: turnover, exposure (% time and average gross), cost drag (costs / gross P&L). Tails: worst day, worst 5 days, CVaR 95%.

Statistical:
- t-stat of daily returns with **Newey–West** standard errors;
- **Probabilistic Sharpe Ratio** and **Deflated Sharpe Ratio**. The DSR uses the trial count from the research budget ledger (RESEARCH_ARCHITECTURE §4);
- minimum track record length;
- bootstrap confidence intervals.

**No single-metric gate.** Promotion gates are multi-criteria, are stored as versioned config, and are evaluated deterministically.

## 5. Validation Pipeline (Loop B)

| Stage | Data | Output |
|---|---|---|
| Backtest | TRAIN | metrics, trades |
| Robustness | TRAIN/VALIDATION | parameter-neighbourhood surface; fragility score = performance drop at ±1 and ±2 grid steps; flag if the best point is an isolated peak |
| Out-of-sample | OOS through `OOSVault` (budget 1) | metrics only |
| Walk-forward | rolling or anchored windows: train → validate → test, roll | per-window metrics, % profitable windows, dispersion, worst window, aggregate (stitched OOS equity) |
| Regime | all evaluated windows, labelled point-in-time | per-regime metrics; **flag any regime with max DD > limit or negative expectancy with n ≥ threshold** |
| Stress | TRAIN + VALIDATION re-runs | 2×/3× slippage, +50% commission, +latency, spread ×3, gap injection, vol spike, liquidity collapse, partial fills, rejects, missing candles, stale data |
| Monte Carlo | trade list / returns | trade-order shuffle, block bootstrap, cost/slippage noise; DD, losing-streak and return distributions; risk-of-ruin. **Labelled SIMULATION** |
| Red team | all of the above | structured critique (AI_AGENT_ARCHITECTURE §3.4) plus deterministic bias checks |
| Paper | live quotes, PAPER mode | same pipeline as live |
| Drift | backtest replay over the paper period vs paper | §6 |

## 6. Drift Engine (Backtest vs Paper)

For the paper period, run the backtest over **the recorded paper-period market data**. This gives the *expected* behaviour on identical inputs. Compare it to paper reality:

| Metric | Test |
|---|---|
| Signal frequency, signal set overlap | Jaccard on (symbol, minute); count ratio |
| Fill rate, rejection rate | proportion test |
| Slippage, latency | distribution compare (KS), mean difference with CI |
| Win rate, expectancy, holding time | difference with CI |
| P&L, drawdown | tracking difference |

Any metric outside its tolerance band produces `DriftReport.status = SIGNIFICANT`, which blocks promotion and, in LIVE, triggers `STRATEGY_KILL`.

## 7. What the Current Backtester Becomes

`BacktestRunner.run_multi_regime_simulation` is kept only as a frozen **legacy characterisation fixture** for Phase 1–2 parity. It is relabelled in output as `LEGACY SYNTHETIC — NOT EVIDENCE`. It is removed after Phase 3 once the event-driven engine replaces it, and that removal is documented in MIGRATION_PLAN.
