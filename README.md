# Trade OS — Local Quant Research System (NSE)

A local-first system for researching, validating and paper-trading systematic strategies on Indian equities (NSE). It is being built toward a full research → validation → guarded-trading platform. See [docs/TARGET_ARCHITECTURE.md](docs/TARGET_ARCHITECTURE.md) and [docs/MIGRATION_PLAN.md](docs/MIGRATION_PLAN.md).

**Status: Phase 1 (foundation).** Paper and demo only. No broker connectivity, and no live trading.

## Core Rule
> **AI may propose. Code must decide. Risk always has veto power.**
> Risk limits live in deterministic code and a versioned config file, never in prompts. Unknown or unreadable state means no trade (fail closed).

## What Exists Today
| Component | State |
|---|---|
| Strategy #001 — Social Momentum v1 | Hand-weighted scorer (`src/trade/strategies/social_momentum/`). Weights and calibration table are **hand-set and unvalidated** |
| Risk engine | Pre-trade gates + fail-closed kill switch (`src/trade/core/risk/`) |
| Paper broker | In-memory simulator with fixed slippage and NSE charges. **Known defect D-001** (cash accounting) |
| Legacy backtest | 500 synthetic candles from a **circular generator**: labelled *NOT EVIDENCE* (see [CURRENT_STATE §6](docs/CURRENT_STATE.md)) |
| Dashboard | Loopback-only web UI with operator token |
| Social recorder | Records real Reddit posts forward for future validation (needs Reddit OAuth credentials) |

Not yet implemented: event-driven backtester, out-of-sample / walk-forward validation, regime analysis, experiment registry, AI agents, broker adapters, live trading. Known defects are tracked in [docs/KNOWN_DEFECTS.md](docs/KNOWN_DEFECTS.md).

## Quickstart
Requires [uv](https://docs.astral.sh/uv/) (it installs Python 3.11+ if needed).

```bash
make setup          # create .venv and install locked dependencies
make test           # full test suite
make test-critical  # safety-critical tests (risk, kill switch, security, golden master)
make lint typecheck
make backtest       # legacy synthetic simulation (NOT evidence of edge)
make paper          # one demonstration paper cycle
make dev            # dashboard at http://127.0.0.1:8080
make record-social  # record real social posts (requires .env credentials)
make help           # all targets
```

Runtime state (kill-switch lockfile, operator token) lives in `var/state/`; override it with `TRADE_STATE_DIR`.

## Sizing & Charges (current configuration)
Risk limits: [`config/risk_limits.yaml`](config/risk_limits.yaml). Charges: [`config/cost_models/nse_intraday.yaml`](config/cost_models/nse_intraday.yaml).

- Capital ₹1,00,000. Max ₹20,000 per trade. Max 2 concurrent positions.
- Daily loss limit ₹2,000 → kill switch. Max drawdown 6% from high-water mark (**configured; enforcement lands in Phase 2**).
- Entries 09:30–14:30 IST. Mandatory square-off 15:10 IST.
- Charges per leg: brokerage `min(₹20, 0.03% of turnover)`, STT 0.025% on sells, exchange 0.00297%, SEBI ₹10/crore, stamp duty 0.003% on buys, and GST 18% on brokerage + exchange + SEBI. A ₹20,000 round trip at +1% costs **₹21.34** under the current code. Verify rates against current NSE/SEBI circulars.

## Repository Layout
```
src/trade/
  core/          Deterministic core (config, market_state, strategy, signals, risk, execution, portfolio, ledger)
  data/          Data providers, recorders, datasets, data quality checks
  strategies/    Strategy implementations (social_momentum, breakout, mean_reversion, trend_following, volatility_expansion, generated)
  research/      Hypotheses, experiments, validation, walk-forward, regimes, stress, red-team
  agents/        Advisory research agents (no trading, risk or approval capabilities)
  backtesting/   Backtest harness & metrics
  paper/         Paper trading execution loop
  brokers/       Broker adapters (paper)
  web/           Dashboard, REST API, terminal monitor
config/          Risk limits, cost models, runtime settings
tests/           Unit, golden-master, risk, security tests
docs/            Audit, architecture, migration plan
```
