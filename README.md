# Autonomous Indian Quant Trading Bot (NSE Intraday MIS)

A zero-error, 3-layer quantitative trading system engineered for **Indian Equities (NSE)** with **₹1,00,000 Capital**.

## Core Philosophy
> *"The models advise, the deterministic code decides."*  
> Risk limits and kill switches are hardcoded in deterministic Python. No probabilistic AI model has authority to override a risk veto, increase position size beyond hard limits, or bypass the kill switch.

---

## Architecture Overview
1. **Layer 1: Slow Brain (Nightly Quant Review):**
   - Reviews every fill and miss after market close.
   - Runs out-of-sample multi-regime backtests before admitting any new rule into `strategy.md`.
2. **Layer 2: Fast Reflex (Sub-Second Calibrated Decision Layer):**
   - Evaluates compact numeric state (< 2ms).
   - Scores 4 fixed outcome questions: Organic momentum, Directional edge, Buying pressure reality, Setup quality.
   - Continuously measures venue calibration with Brier Score.
3. **Layer 3: Deterministic Execution Core:**
   - Pre-trade risk gates (Trading hours, Circuit limits, ADV, OBI, Spreads).
   - Atomic OCO bracket generator (+1.00% Take Profit / -0.70% Hard Stop Loss).
   - Hardware/software emergency Kill Switch.

---

## Repository Layout
```
core/            Deterministic core (config, market_state, strategy, signals, risk, execution, portfolio, ledger)
data/            Data providers, datasets, data quality checks
strategies/      Strategy implementations (social_momentum, breakout, mean_reversion, generated)
research/        Experiments, hypotheses, validation, walk-forward, red-team
agents/          Advisory research agents (researcher, strategy_builder, validator, red_team)
backtesting/     Backtest harness & statistical gates
paper/           Paper trading execution loop
brokers/         Broker adapters (paper)
web/             Web dashboard, REST API, terminal monitor
tests/           Unit & integration tests
```

---

## Sizing & Indian Market Friction
- **Capital:** ₹1,00,000 INR
- **Max Trade Size:** ₹20,000 (20% NAV cash / 1x MIS allocation)
- **Max Concurrent Exposure:** 2 positions (Max ₹40,000 portfolio heat)
- **Daily Loss Ceiling (Kill Switch):** ₹2,000 (2% max daily loss)
- **Mandatory EOD Square-off:** 03:10 PM IST (avoids broker MIS penalty charges)
- **Indian Statutory Taxes Deducted:**
  - Brokerage: ₹20 buy + ₹20 sell (Dhan / Zerodha / Fyers)
  - STT: 0.025% on sell turnover
  - Exchange Turnover: 0.00297% NSE
  - SEBI Fee: ₹10/crore
  - Stamp Duty: 0.003% buy
  - GST: 18% on statutory fees
  - **Roundtrip friction per ₹20k trade:** ~₹54.26

---

## Quickstart Commands

### 1. Run Comprehensive Unit & Integration Tests
```bash
python3 -m unittest discover tests
```

### 2. Run Historical Multi-Regime Backtest Gate
Tests Sharpe, Max Drawdown, Hit Rate, and out-of-sample t-statistic:
```bash
python3 -m backtesting
```

### 3. Run Live Execution Loop Demonstration
```bash
python3 -m paper
```

### 4. Render Live Monitoring Dashboard
```bash
python3 -m web.terminal
```
