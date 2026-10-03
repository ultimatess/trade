# Target Architecture — Local Quant Research OS

## 1. Central Invariant

```
AI THINKS → STRATEGY PROPOSES → DETERMINISTIC SYSTEM → RISK DECIDES → EXECUTION ACTS
```

- **AI may propose. Code must decide. Risk always has veto power.**
- **Fail closed:** any unknown, stale, unavailable or inconsistent state results in `DENY`.
- **Authority is enforced by capability, not by instruction.** An agent cannot bypass risk because no code path gives it the ability to. Telling it not to is not the protection.

## 2. Process & Trust Boundaries

```
┌──────────────────────────── UNTRUSTED ZONE ─────────────────────────────┐
│  Market data · news · social text · web pages · LLM outputs · generated │
│  strategy code · agent proposals                                        │
└───────────────┬─────────────────────────────────────────┬───────────────┘
                │ typed, schema-validated data only       │ proposals only
                ▼                                         ▼
┌──────────────────────────┐              ┌──────────────────────────────┐
│  RESEARCH PLANE           │              │  AGENT RUNTIME (sandboxed)   │
│  (process: research)      │◄─read-only──│  ResearchAgent, RedTeam, …   │
│  backtests, experiments,  │   tools      │  no secrets, no broker,      │
│  validation, registry     │              │  no write to ops tables      │
└───────────┬──────────────┘              └──────────────────────────────┘
            │ approved, immutable StrategyVersion + human approval record
            ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  TRADING PLANE (process: trader) — deterministic, no LLM calls           │
│  MarketState → Strategy → Signal → Sizer → RiskEngine → ExecutionAdapter │
│  → Portfolio → Ledger/Journal   · Reconciler · KillSwitch · LiveLock     │
└──────────────────────────────────────────────────────────────────────────┘
            │ events (append-only)
            ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  OPERATOR (human) — Command Center UI, approvals, kill/reset, live unlock│
└──────────────────────────────────────────────────────────────────────────┘
```

Rules:
1. The **trading plane never imports agent code** and never calls an LLM. The fast decision layer (JEV) is used only as a strategy component that returns numbers. It must be deterministic and versioned, or run locally from a pinned model artifact. Its output is a `Signal` field. It never produces an order.
2. **Broker credentials exist only in the trading-plane process environment.** The research plane and the agent runtime never receive them.
3. **Risk limits are loaded once at trader start-up** from a versioned file. Their hash is written to the ledger. No API mutates them at runtime.
4. **Lifecycle promotions and live unlock** require an operator approval record. The approval is created through the UI with a local operator credential that the agent runtime does not hold.

## 3. Three Loops

| Loop | Plane | Autonomy | Can trade? |
|---|---|---|---|
| A — Research | Research + agents | High (within research budget) | No |
| B — Validation | Research | Deterministic pipeline; agents only add red-team reports | No (paper only, through trading plane in PAPER mode) |
| C — Production | Trading | None for AI; deterministic | Yes, if the strategy is LIVE and the live lock is open |

Production anomalies emit `STRATEGY_SUSPENDED` plus a `ResearchArtifact` ticket into Loop A. They **never** modify the running strategy version.

## 4. Component Map

| Component | Package | Responsibility |
|---|---|---|
| Market data interfaces | `trade.data.providers` | `HistoricalDataProvider`, `RealtimeDataProvider`, `QuoteProvider`, `OrderBookProvider`, `InstrumentProvider`, `TradingCalendarProvider` |
| Datasets | `trade.data.datasets` | Versioned, content-hashed Parquet datasets; partitions TRAIN/VALIDATION/OOS |
| Data quality | `trade.data.quality` | Gaps, staleness, outliers, monotonic timestamps, split/corporate-action checks |
| Market state | `trade.core.market_state` | Point-in-time `SnapshotBuilder`, feature registry with lineage |
| Strategy contract | `trade.core.strategy` | `Strategy` ABC, `StrategySpec` (YAML), `Signal`, lifecycle state machine, calibration |
| Signals | `trade.core.signals` | `Signal` model, validation, persistence |
| Sizing | `trade.core.risk.sizing` | Fixed, % equity, risk-per-trade, ATR, vol-target, gated fractional Kelly |
| Risk | `trade.core.risk` | `RiskEngine` (Allow/Deny), `RiskLimits`, kill-switch hierarchy, live lock |
| Execution | `trade.core.execution` | `OrderIntent`, `Order`, `Fill`, `CostModel`, `SlippageModel`, `ExecutionAdapter` protocol |
| Portfolio | `trade.core.portfolio` | Positions, cash, exposure, P&L, per-strategy attribution |
| Ledger | `trade.core.ledger` | Append-only event ledger, trade journal |
| Backtesting | `trade.backtesting` | Event loop, `SimulatedExchange`, metrics |
| Research | `trade.research` | Hypotheses, experiments, budgets, OOS vault, walk-forward, regimes, robustness, stress, Monte Carlo, drift |
| Agents | `trade.agents` | Agent runtime, tool registry, schemas, model-provider interface |
| Paper | `trade.paper` | Trading-plane runner in PAPER mode |
| Brokers | `trade.brokers` | `PaperBroker`, later real adapters + `Reconciler` |
| Web | `trade.web` | API + UI |

## 5. One Strategy Interface, Three Modes

```
DataSource ─► SnapshotBuilder ─► Strategy ─► Signal ─► Sizer ─► RiskEngine ─► ExecutionAdapter ─► Portfolio ─► Ledger
   │                                                                              │
   ├── BACKTEST: HistoricalDataProvider                                           ├── SimulatedExchange
   ├── PAPER:    RealtimeDataProvider                                             ├── PaperBroker (SimulatedExchange on live quotes)
   └── LIVE:     RealtimeDataProvider                                             └── BrokerAdapter (+ Reconciler)
```

The code from `SnapshotBuilder` through `Ledger` is the same object graph in every mode. A `Clock` abstraction (`SimulatedClock` or `WallClock`) is the only other difference.

## 6. Target Directory Structure

A `src/` layout under a single `trade` package. This resolves the `core`/`data`/`web` name collisions noted in CURRENT_STATE §12.

```
trade/                               (repo root)
├── pyproject.toml   Makefile   .env.example   AGENTS.md   README.md
├── config/
│   ├── risk_limits.yaml             # loaded once by trader; hashed into ledger
│   ├── cost_models/{nse_intraday,nse_delivery,us_equities}.yaml
│   └── runtime.yaml
├── datasets/                        # gitignored payloads; manifests committed
├── src/trade/
│   ├── core/{market_state,strategy,signals,risk,execution,portfolio,ledger}/
│   ├── data/{providers,datasets,quality}/
│   ├── strategies/
│   │   ├── social_momentum/v1/{strategy.yaml,strategy.py,README.md}
│   │   ├── breakout/v1/   mean_reversion/v1/   trend_following/v1/
│   │   ├── volatility_expansion/v1/
│   │   └── generated/                # agent-produced candidates; DRAFT only
│   ├── research/{hypotheses,experiments,validation,walk_forward,regimes,stress,red_team,monte_carlo,drift}/
│   ├── agents/{runtime,researcher,strategy_builder,validator,red_team,trade_reviewer,regime,portfolio,operations}/
│   ├── backtesting/   paper/   brokers/   web/
│   └── db/                          # SQLAlchemy models + Alembic migrations
├── tests/{unit,integration,contract,leakage,risk,security,failure,e2e}/
└── docs/   docs/tutorials/
```

## 7. Storage

| Store | Technology | Holds | Why |
|---|---|---|---|
| Operational / registry DB | **SQLite (WAL)** — operator decision 2026-10-03. Models kept Postgres-portable (SQLAlchemy + Alembic) | Strategies, versions, hypotheses, experiments, signals, risk decisions, orders, fills, positions, ledger, journal, approvals | Zero-infrastructure `make setup`; Postgres when running multiple processes concurrently or remotely |
| Market data | **Parquet** files, queried with **DuckDB** | OHLCV, quotes, depth, social counts, features | Columnar, immutable, content-hashable, fast local analytics |
| Artifacts | Content-addressed directory (`artifacts/<sha256>`) | Equity curves, trade lists, reports, agent transcripts | Reproducibility; immutable by construction |

No Redis, no message broker, no containers are required. Each would be added only with a documented reason.

## 8. Technology Choices

| Concern | Choice | Notes |
|---|---|---|
| Language | Python ≥ 3.11 | Existing codebase |
| Schemas | Pydantic v2 | Strategy specs, Signal, agent I/O, API |
| Numerics | NumPy, pandas or Polars | Polars preferred for snapshot building |
| DB | SQLAlchemy 2 + Alembic | SQLite/Postgres portable |
| API | FastAPI, bound to `127.0.0.1`, with local operator token for mutating routes | Replaces the stdlib server |
| UI | Locally built static frontend (no runtime CDN) | Framework decision deferred to the UI phase |
| Tests | pytest, hypothesis (property-based), pytest markers `critical` | |
| Tooling | ruff (format + lint), mypy (strict on `core/`), uv or pip-tools lockfile | |
| CI | GitHub Actions | |

## 9. Modes and Live Lock

`MODE ∈ {DEMO, BACKTEST, PAPER, LIVE}`. LIVE requires all of the following:
- `LiveLock.state == UNLOCKED`, which needs every unlock condition in RISK_ARCHITECTURE §9;
- `StrategyVersion.lifecycle == LIVE`, which needs a human approval record;
- broker reconciliation `OK` within its freshness window;
- no active kill switch at any scope.

The default and the post-restart state is **LOCKED**.

## 10. Database Design

Operational DB (SQLAlchemy models, Alembic migrations). **Append-only** tables accept inserts only. This is enforced by repository code, plus DB triggers on Postgres. Tables marked *versioned* are never updated in place; a change creates a new version row.

| Entity | Key fields | Mutability |
|---|---|---|
| User | id, name, role (`operator`/`viewer`), token_hash | mutable |
| Account | id, broker, mode (PAPER/LIVE), base_currency | mutable (status only) |
| Instrument | id, symbol, exchange, isin, lot_size, tick_size, band_pct, valid_from/to | versioned (point-in-time) |
| Dataset | id, version, manifest_sha256, partitions, quality_status, parent_version | versioned |
| MarketData | *(Parquet; referenced by Dataset)* | immutable files |
| MarketSnapshot | snapshot_id (hash), as_of, symbol, feature_values (JSON), dataset ref | append-only |
| Strategy | id (slug), name, owner, champion_version | mutable pointer |
| StrategyVersion | strategy_id, version, spec_yaml, code_sha256, hypothesis_id, lifecycle_state, created_by | versioned; lifecycle changes recorded as events |
| StrategyParameter | strategy_version, name, value, search_range, provenance | immutable per version |
| Hypothesis | id, statement, null, falsification_criteria (frozen on first experiment), budget, status, parent_id | criteria immutable after start |
| ResearchBudgetLedger | hypothesis_id, kind (experiment/param_trial/feature/variant/oos), delta, experiment_id | append-only |
| Experiment | id, hypothesis_id, strategy_version, dataset@version, params, commit, seed, cost_model@v, slippage_model@v, env, status, decision | immutable after completion |
| BacktestRun | id, experiment_id, partition, metrics (JSON), trade_list_sha256, artifact refs | immutable |
| BacktestTrade | backtest_run_id, trade fields | immutable |
| WalkForwardRun / WalkForwardWindow | run id, window bounds, chosen params, metrics | immutable |
| Signal | signal fields (STRATEGY_CONTRACT §3), mode, correlation_id | append-only |
| RiskDecision | decision fields (RISK_ARCHITECTURE §1) | append-only |
| Order | client_order_id (idempotency key), intent_id, broker_order_id, state | state transitions recorded as events |
| Fill | order_id, qty, price, fees, liquidity flag, exchange_time, received_at | append-only |
| Position | account, strategy_version, symbol, qty, avg_price | derived/materialised from fills |
| PortfolioSnapshot | account, as_of, cash, equity, exposure, drawdown, per-strategy attribution | append-only |
| TradeJournal | trade_id, strategy_version, regime, snapshot_id, signal_id, decision_id, order ids, fill ids, slippage, pnl, exit_reason, expected vs actual | append-only |
| SystemEvent (ledger) | id, ts, type (directive §42 list), correlation_id, strategy_id/version, component, input_hash, output, reason | append-only, hash-chained (each row stores prev hash) |
| Alert | id, severity, source, status, ack_by | mutable status |
| Approval | id, subject (strategy_version / live_unlock / risk_config), operator_id, evidence_refs, ts | append-only |
| KillSwitchState | scope, key, active, reason, triggered_at, reset_by | events + current view |
| AgentRun | id, agent, model, prompt_hash, inputs_hash, tool_calls, output, status, cost | append-only |
| ResearchArtifact | id, kind, claims (with evidence refs), verified, agent_run_id | immutable |
| CalibrationResult | strategy_version, n, brier, ece, curve, dataset/partition | immutable |
| DriftReport | strategy_version, period, metrics, status | immutable |
