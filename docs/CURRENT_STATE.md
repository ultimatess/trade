# Current State — Repository Audit (Phase 0)

Audited revision: `1b98aab` (branch `refactor/target-layout`), 2026-10-03.

> **Revision note (Phase 1):** this document is the Phase 0 snapshot. File paths have since moved under `src/trade/` and the safety/security findings have been fixed. Current status of every finding is in [KNOWN_DEFECTS.md](KNOWN_DEFECTS.md).
Scope: every file in the repository. All findings marked **[verified]** were reproduced by running the code; the rest come from reading it.

---

## 1. Existing Architecture

A single-strategy, single-process, in-memory Python application. It uses only the standard library (no third-party dependencies, no `requirements.txt`, no `pyproject.toml`).

It describes itself as a "3-layer" system:

| Layer | Claimed | Actually implemented |
|---|---|---|
| L1 "Slow Brain" (nightly review) | Reviews fills, runs OOS multi-regime backtests before admitting rules | **Not implemented.** No code exists. |
| L2 "Fast Reflex" | Calibrated probabilistic scorer, Brier monitoring | Hand-weighted linear score + hard-coded calibration table (`strategies/social_momentum/reflex.py`) |
| L3 Deterministic core | Risk gates, OCO brackets, kill switch | Risk gate function, in-memory paper broker, lockfile kill switch |

Total: ~1,760 lines of Python, 1 HTML dashboard (586 lines).

## 2. Existing Modules

| Module | Responsibility | Lines (approx.) |
|---|---|---|
| `core/config.py` | Frozen dataclass of every constant: capital, risk limits, gates, NSE charges, broker type | 55 |
| `core/*/models.py` | `MarketSnapshot`, `SocialSignal`, `ReflexDecision`, `Order`, `Position`, `TradeResult` | 125 |
| `core/risk/engine.py` | `RiskEngine`: 10 pre-trade gates + lockfile kill switch | 105 |
| `core/strategy/calibration.py` | `CalibrationEngine`: Brier score + piecewise-linear calibration map | 45 |
| `core/execution/charges.py` | `IndianTaxCalculator`: brokerage, STT, exchange, SEBI, stamp, GST | 55 |
| `strategies/social_momentum/reflex.py` | `FastReflexScorer`: p_organic, p_win, quality, quarter-Kelly | 75 |
| `data/providers/social.py` | `SocialMomentumScanner`: mention velocity z-score, duplicate-text spam score, StockTwits fetch (unused) | 90 |
| `brokers/paper.py` | `IndianPaperBroker`: entry, OCO evaluation per tick, timeout, flatten | 180 |
| `backtesting/engine.py` | `BacktestMetrics` + `BacktestRunner` (synthetic generator + loop) | 210 |
| `paper/trading_system.py` | `QuantTradingSystem.run_single_cycle` — one demo iteration | 120 |
| `web/server.py` | Stdlib HTTP server, REST API, background random-tick worker | 380 |
| `web/static/index.html` | Tailwind (CDN) dashboard polling `/api/status` | 586 |
| `web/terminal.py` | Text dashboard renderer | 55 |
| `tests/test_quant_system.py` | 7 unittest cases | 160 |

## 3. Current Data Flow

```
(caller supplies price, depth, rvol, ADV, list of tweet strings, "HH:MM" string)
        │
        ▼
SocialMomentumScanner.sanitize_and_extract_signal  → SocialSignal (numeric)
        │
        ▼
MarketSnapshot built inline by the caller
   bid/ask   = price ± 0.04%         (fabricated)
   vwap      = price × 0.999         (fabricated: price is always above VWAP)
   circuits  = price × 1.10 / 0.90   (fabricated: circuit gate can never fire)
   depth     = hard-coded 45,000 / 15,000 in web server (OBI is always 0.50)
   ADV       = hard-coded ₹18 Cr
        │
        ▼
RiskEngine.validate_pre_trade_gates → FastReflexScorer.evaluate → IndianPaperBroker.submit_bracket_entry
```

No market data source exists. There is no historical data, no data on disk, and no live feed. `fetch_stocktwits_trending()` is never called.

## 4. Current Strategy Flow (Social Momentum — to become Strategy #001)

Specified in `strategy.md`. Implemented as:

1. **Pre-trade gates** (`RiskEngine`): kill switch, daily loss, 09:30–14:30 window, max 2 positions, 1.5% circuit buffer, spread ≤ 0.15%, ADV ≥ ₹10 Cr, OBI ≥ 0.35, RVOL ≥ 3.0, spam score ≤ 0.30.
2. **Reflex scoring** (`FastReflexScorer`):
   - `p_organic = 0.5·verified_ratio + 0.3·clip((z−2)/4) + 0.2·(1−spam)`
   - `p_win_raw = 0.45·(OBI+1)/2 + 0.35·min(1, RVOL/8) + 0.20·(1 if price ≥ VWAP else 0.4)`
   - `p_win_cal = interp(calibration_map, p_win_raw)` — **the map is a hard-coded table, not fitted to data**
   - `quality = 30·p_organic + 50·p_win_raw + 20·min(1, RVOL/5)`
   - Vetoes: p_organic < 0.70, p_win_cal < 0.65, quality < 75
   - Size: `min(0.25 · Kelly(p_win_cal, b=0.75), 0.20)` of cash, then capped at ₹20,000 by the broker
3. **Execution** (`IndianPaperBroker`): fill = price + 0.05%; target +1.00%, stop −0.70%, timeout 35 minutes; long-only.

Note: these gates sit in `RiskEngine`, but several (OBI, RVOL, spam score) are **strategy entry conditions**, not risk limits. That mixing must be undone (see §14).

## 5. Current Risk Model

| Control | Status |
|---|---|
| Kill switch | A lockfile at a **relative** path (`trading.lock`), so it depends on the current working directory. The backtest uses `/tmp/backtest_trading.lock` and **clears it automatically at start**. |
| Kill switch write failure | Logged and swallowed — **fails open** for the manual kill path. |
| Daily loss | Counts only the sum of *losing* realized trades. Ignores winners, ignores unrealized loss (`strategy.md` says it includes unrealized). Never resets daily. |
| Max drawdown (`MAX_ACCOUNT_DRAWDOWN_PCT = 8%`) | **Defined but never enforced.** `current_equity` is passed to the risk engine and ignored. `strategy.md` says 6% weekly; the config says 8%. |
| Order-level limits | **None.** The risk check runs *before* sizing. The size the broker computes is never risk-checked. |
| Max positions | Enforced (2). |
| Stale data, clock, reconciliation, symbol concentration, order rate | Not present. |
| EOD square-off | Only in `paper/trading_system.py`, and only if the caller passes a time string ≥ 15:10. The backtest hard-codes `"11:30"`, so square-off is never exercised. |

## 6. Current Backtest Model

`BacktestRunner.run_multi_regime_simulation` — **[verified] it is circular and cannot provide evidence of an edge.**

- **One shared price series for four symbols.** `current_price` is a single variable rotated across TATASTEEL/RELIANCE/ZOMATO/HDFCBANK.
- **The regime is a deterministic function of the loop index** (`i % 3`, `i % 7`).
- **The features are drawn from the same regime label as the price shock.** In TREND candles the generator *simultaneously* draws high RVOL / OBI / z-score **and** a positive price shock of +0.2–1.2%. Only TREND candles can pass the OBI ≥ 0.35 and RVOL ≥ 3 gates. The features therefore encode the forward return by construction.
- **The generator has a positive expected drift**: about +0.23% from TREND minus about 0.1% from DUMP per candle.
- Result across seeds 1, 2, 3, 42, 123: Sharpe **9.4 – 19.4**, hit rate 75–84%, **all "PASS"**. These numbers are artefacts of the generator.
- "2-year historical backtest" (in the README and console output) = 500 one-minute synthetic candles ≈ 8.3 trading hours.
- "Out-of-sample t-statistic": there is no in-sample/out-of-sample split. It is a one-sample t-test on all trades.
- Annualisation: `trades_per_year = min(750, max(250, n·5))` — arbitrary.
- `returns = pnl / MAX_CAPITAL_PER_TRADE` even though positions are sized well below ₹20k.
- Stops always fill at `stop × 0.9995` regardless of gaps (optimistic). Take-profits fill exactly at target.
- Exits are evaluated on the tick *after* entry; with one shared series, the "next tick" for a symbol is 4 candles later.

## 7. Current Dashboard

- `web/server.py`: `ThreadingHTTPServer` bound to **`0.0.0.0:8080`**, `Access-Control-Allow-Origin: *`, **no authentication**. Endpoints: `/api/status`, `/api/execute_cycle`, `/api/run_backtest`, `/api/kill_switch`, `/api/unlock`, `/api/reset_account`, `/api/toggle_bot`.
- The background "autonomous" worker generates random prices and three fixed template tweets every 4 seconds, with `time_str="11:30"` hard-coded.
- `index.html` builds tables with `innerHTML` from server data. That data includes user-supplied `symbol` strings, so it is **vulnerable to stored XSS**. It loads Tailwind from a CDN (not local-first).
- `web/terminal.py`: a text renderer of the broker state.

## 8. Existing Tests

7 `unittest` cases, all passing:

| Test | Real assertion strength |
|---|---|
| `test_indian_tax_calculator` | Weak — `assertAlmostEqual(brokerage, 12.06, delta=30)` accepts anything from −18 to 42 |
| `test_risk_engine_trading_hours_veto` | OK |
| `test_risk_engine_circuit_proximity_veto` | OK |
| `test_kill_switch_triggers_on_daily_loss` | OK |
| `test_fast_reflex_scoring_and_sizing` | OK, single point |
| `test_paper_broker_oco_execution` | Take-profit only; no stop-loss, timeout or flatten test |
| `test_backtest_simulation_execution` | Checks only that dict keys exist |

There are no tests for the web server, the scanner, the paper loop, the calibration, or the broker's cash accounting.

## 9. Existing Weaknesses (correctness defects)

| ID | Defect | Evidence |
|---|---|---|
| D-001 | **Paper broker never debits cash on entry, but credits `notional + pnl` on exit.** Cash and NAV inflate by the full notional on every trade. Because sizing uses `cash × fraction`, position sizes grow over a backtest. | **[verified]** ₹100k account, one trade with net +₹178.65 → cash ₹120,188.65 |
| D-002 | `CalibrationEngine.record_outcome` is always called with the constant `0.65`, not the forecast. The Brier score is meaningless. | `paper/trading_system.py`, `web/server.py` |
| D-003 | The calibration map is hard-coded. It is described as "empirical", but no data was used. | `core/strategy/calibration.py` |
| D-004 | Kelly payoff `b = 0.75` is derived from ₹54.26 friction (`strategy.md`), but the code computes about ₹21.34. | **[verified]** `charges(100,101,200).total_charges = 21.34` |
| D-005 | Brokerage is `min(₹20, 0.03%)` in code; `strategy.md` and the README say a flat ₹20 + ₹20. The breakeven win rate of 57.13% in docs is therefore inconsistent with the code. | |
| D-006 | Max drawdown limit not enforced; daily loss excludes unrealized P&L and never resets. | §5 |
| D-007 | Kill-switch lockfile path is relative to the current working directory; a write failure is swallowed. | `core/risk/engine.py` |
| D-008 | The circular synthetic backtest (§6) is presented as a passing validation gate. | **[verified]** |
| D-009 | Positions are keyed by symbol only. There is no strategy ownership and no short support. | `brokers/paper.py` |
| D-010 | Velocity z-score: every tweet in a cycle is recorded at the same timestamp. The "verified ratio" is synthesised from the duplication rate (comment says "Simulated"). | `data/providers/social.py` |
| D-011 | `toggle_bot` can start multiple worker threads if toggled quickly. | `web/server.py` |
| D-012 | All state is in memory. A restart loses positions, orders and history. | |

## 10. Quant / Research Risks

1. **No real data, and the strategy depends on data that is hard to obtain historically.** Strategy #001 needs (a) historical cashtag mention streams for NSE symbols and (b) L2 order-book depth for OBI. StockTwits coverage of NSE is thin, and historical X/Twitter data is not freely available. NSE depth history requires a paid vendor or self-recorded broker feeds. **Until that data exists, Strategy #001 cannot be validated. It can only be parity-tested.**
2. The scoring weights (0.5/0.3/0.2, 0.45/0.35/0.20, 30/50/20) and thresholds were chosen by hand, with no recorded search. The number of trials behind them is unknown, so any future backtest on the same idea must count them as already data-snooped.
3. +1.0% / −0.7% bracket: the breakeven hit rate is ≈ 41% gross. Net of the ₹21 code-computed friction (≈0.107% of ₹20k) and the modelled stop slippage, it is ≈ 49% (win ≈ +0.89%, loss ≈ −0.86%). The edge must survive 1-minute NSE slippage, which is often larger than the 0.05% modelled for small caps.
4. Intrabar ambiguity is not modelled: within one bar, which of TP and SL hit first.
5. Upper and lower circuit bands are fabricated at ±10%. Real NSE bands vary (2/5/10/20%), and F&O stocks have dynamic bands instead.
6. Statutory rates (STT, exchange, SEBI, stamp, GST) are hard-coded with no effective date. They change through regulatory circulars.

## 11. Security Risks

| ID | Risk | Severity |
|---|---|---|
| S-001 | Unauthenticated mutating API on `0.0.0.0` — anyone on the LAN can unlock the kill switch, reset the account or start the bot | High |
| S-002 | `Access-Control-Allow-Origin: *` plus a permissive `OPTIONS` handler → any website visited in the operator's browser can call `/api/unlock` | High |
| S-003 | Stored XSS through `symbol` and log messages rendered with `innerHTML` | Medium |
| S-004 | Kill switch fails open on write error; lockfile location depends on the working directory | High (safety) |
| S-005 | Tailwind loaded from a third-party CDN at runtime (supply chain; breaks offline) | Low |
| S-006 | `BROKER_TYPE` env var exists with a `"DHAN"` value but no adapter; there is no `.env.example` and no secrets policy | Low today, High once a broker is added |
| S-007 | Social text is "sanitized" only in the sense that it isn't fed to an LLM. This is not yet a real isolation boundary. | Low today |

## 12. Technical Debt

- No packaging, dependency manifest, lint, type checking, CI, or Makefile.
- Top-level package names (`core`, `data`, `web`, `tests`) can collide with installed packages; imports work only from the repo root.
- Configuration is one flat dataclass that mixes risk limits, strategy parameters, cost model and runtime settings.
- Strategy entry rules live inside the risk engine.
- The `MarketSnapshot` construction is duplicated three times (backtest, paper loop, web) with different fabricated values.
- Logging is unstructured f-strings. There is no correlation IDs and no persistent event ledger.
- Marketing claims in the docs ("zero-error", "2-year backtest", "out-of-sample", "sub-second", "hardware kill switch", "atomic OCO") are not supported by the code.

## 13. What Should Remain

- **Social Momentum strategy logic**, preserved bit-for-bit as Strategy #001 v1 (including its hand-set weights), with golden-master parity tests.
- **NSE statutory charge calculation** → becomes the `nse_intraday` cost model configuration.
- **The pre-trade gate concepts**: circuit buffer, spread, ADV, trading window, max positions, daily loss.
- **The principle "models advise, deterministic code decides"** — it is correct and becomes the system's central invariant.
- Frozen-dataclass style for immutable models.
- The kill-switch concept (to be hardened into a hierarchy).

## 14. What Should Be Refactored

| From | To |
|---|---|
| `TradingConfig` | `RiskLimits` (frozen, hashed), `CostModel` (YAML), strategy parameters (`strategy.yaml`), `RuntimeSettings` |
| OBI / RVOL / spam gates in `RiskEngine` | Strategy #001 entry conditions |
| `RiskEngine.validate_pre_trade_gates(snapshot, signal, …)` | `RiskEngine.evaluate(OrderIntent, PortfolioState, MarketState) → Allow \| Deny(reason_codes)`, run *after* sizing |
| `FastReflexScorer` | `SocialMomentumV1(Strategy)` producing a structured `Signal` |
| `IndianPaperBroker` | `Portfolio` + `Ledger` + `ExecutionAdapter` (`SimulatedExchange`, `PaperBroker`) |
| Inline snapshot construction ×3 | One point-in-time `SnapshotBuilder` |
| `SocialMomentumScanner` | `SocialDataProvider` (ingestion) + feature functions (velocity, duplication) |

## 15. What Should Be Replaced

- `BacktestRunner`'s synthetic generator → event-driven backtester plus deterministic scenario datasets. In those datasets, the generator emits **only prices, volumes and order books**, never pre-computed features.
- `BacktestMetrics` gates → validation engine with a full metric suite and multiple-testing correction.
- Hard-coded calibration map → fitted calibration with a minimum sample gate. Until fitted, Kelly sizing is disabled.
- Stdlib HTTP server + CDN dashboard → authenticated, localhost-only API and a locally built UI.
- Relative-path lockfile → persisted kill-switch state in the operational database, plus an absolute-path lockfile, failing closed.

## 16. Target Migration Plan (summary)

See `docs/MIGRATION_PLAN.md` for detail.

| Phase | Outcome |
|---|---|
| 0 | This audit + architecture documents (**current**) |
| 1 | Packaging, tooling, CI, golden-master characterisation, config split, safety and security hardening. **No strategy behaviour change.** |
| 2 | `Strategy` contract; Social Momentum → Strategy #001 v1 with parity tests; defect fixes D-001…D-006 as explicit, separately reviewed golden updates |
| 3 | Event-driven backtester, point-in-time snapshots, leakage tests |
| 4–15 | As listed in the build directive |
